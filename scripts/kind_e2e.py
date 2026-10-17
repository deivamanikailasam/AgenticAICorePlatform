"""The platform on a real Kubernetes cluster (Step 38): kind, Argo CD, KEDA, the dev overlay
synced by Argo CD from this commit, against the Project 0 stand-in. Run by CI (deploy-e2e).

Needs Docker, kind, kubectl and kustomize; the stand-in up and provisioned (`just standin-up`,
`just provision`, `just migrate`) and the commit's images published (the images workflow).

1. a kind cluster (pinned node image) with Argo CD and KEDA (pinned releases, images by digest);
2. **admission**: every overlay (dev, staging, prod) and the Argo CD apps applied as a
   server-side dry run, so the real API server, Pod Security "restricted" and KEDA's webhook judge
   every object;
3. the stand-in bridged in as the `rag-platform` namespace (its containers as endpoints);
   OpenBao's Kubernetes auth configured for the dev namespace's service accounts, with a dev KV
   mount;
4. **GitOps**: the dev Application synced by Argo CD from this commit, images pinned to the
   digests this commit published; the pre-sync migration Job runs; everything becomes healthy;
5. **smoke**: a client in a namespace the ingress policy admits gets a Keycloak token and runs
   hello-workflow through agent-api; a client in any other namespace can't reach agent-api.

Writes evals/results/deploy/kind-e2e.json; exit 1 on any failure.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from scripts.deploy import _digest

ROOT = Path(__file__).resolve().parents[1]
CLUSTER = "agent-e2e"
NODE = f"{CLUSTER}-control-plane"
NODE_IMAGE = (
    "docker.io/kindest/node:v1.37.0"
    "@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
)
P0_NETWORK = os.environ.get("P0_NETWORK", "rag-platform")
# the stand-in's services the platform reaches, with their container ports
BRIDGED = {
    "postgres": [5432],
    "valkey": [6379],
    "keycloak": [8080],
    "openbao": [8200],
    "opa": [8181],
    "litellm": [4000],
    "otel-collector": [4317, 4318, 13133],
    "presidio-analyzer": [3000],
    "presidio-anonymizer": [3000],
}
ENV = "dev"
NS = f"agent-platform-{ENV}"
SERVICE_ACCOUNTS = ["agent-api", "agent-worker", "mcp-gateway", "a2a-gateway", "agent-db-migrate"]
BAO = os.environ.get("P0_OPENBAO_URL", "http://127.0.0.1:8200")
BAO_TOKEN = os.environ.get("OPENBAO_ADMIN_TOKEN", "")
DB_IN_CLUSTER = "postgresql://agent_app:standin-only@postgres.rag-platform.svc:5432/agent"
results: dict[str, Any] = {"steps": {}}


def sh(*cmd: str, stdin: str | None = None, check: bool = True) -> str:
    proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True)  # noqa: S603
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} failed: {proc.stderr.strip()[-2000:]}")
    return proc.stdout


def kubectl(*args: str, stdin: str | None = None, check: bool = True) -> str:
    return sh("kubectl", *args, stdin=stdin, check=check)


def build(path: str) -> str:
    return sh("kustomize", "build", "--load-restrictor", "LoadRestrictionsNone", str(ROOT / path))


def step(name: str, ok: bool, detail: Any = None) -> None:
    results["steps"][name] = {"ok": ok, **({"detail": detail} if detail is not None else {})}
    print(
        f"kind-e2e: {name}: {'ok' if ok else 'FAILED'}{f' ({detail})' if detail and not ok else ''}"
    )
    if not ok:
        raise RuntimeError(name)


# ---------------------------------------------------------------- 1. cluster and add-ons


def cluster() -> None:
    sh("kind", "delete", "cluster", "--name", CLUSTER, check=False)
    sh("kind", "create", "cluster", "--name", CLUSTER, "--image", NODE_IMAGE, "--wait", "180s")
    sh(sys.executable, "-m", "scripts.cluster_addons")
    kubectl("create", "namespace", "argocd")
    kubectl("apply", "--server-side", "-f", "-", stdin=build("deploy/argocd/install"))
    kubectl("apply", "--server-side", "-f", "-", stdin=build("deploy/keda"))
    for ns, deploy in (
        ("argocd", "argocd-server"),
        ("argocd", "argocd-repo-server"),
        ("keda", "keda-operator"),
        ("keda", "keda-admission-webhooks"),
    ):
        kubectl("-n", ns, "rollout", "status", f"deploy/{deploy}", "--timeout=300s")
    kubectl(
        "-n", "argocd", "rollout", "status", "statefulset/argocd-application-controller", "--timeout=300s"
    )  # fmt: skip
    step("cluster with Argo CD and KEDA", True)


# ---------------------------------------------------------------- 2. admission


def admission() -> None:
    checked: dict[str, int] = {}
    for env in ("dev", "staging", "prod"):
        docs = [d for d in yaml.safe_load_all(build(f"deploy/overlays/{env}")) if d]
        namespaces = [d for d in docs if d["kind"] == "Namespace"]
        kubectl("apply", "-f", "-", stdin=yaml.safe_dump_all(namespaces))
        rest = [d for d in docs if d["kind"] != "Namespace"]
        kubectl("apply", "--dry-run=server", "-f", "-", stdin=yaml.safe_dump_all(rest))
        checked[env] = len(docs)
    apps = [d for d in yaml.safe_load_all(build("deploy/argocd")) if d]
    kubectl("apply", "--dry-run=server", "-f", "-", stdin=yaml.safe_dump_all(apps))
    checked["argocd"] = len(apps)
    step("server-side admission of every overlay", True, checked)


# ---------------------------------------------------------------- 3. Project 0 and OpenBao


def bridge() -> None:
    sh("docker", "network", "connect", P0_NETWORK, NODE, check=False)
    subnet = json.loads(sh("docker", "network", "inspect", P0_NETWORK))[0]["IPAM"]["Config"][0][
        "Subnet"
    ]
    kubectl("create", "namespace", "rag-platform")
    objs: list[dict[str, Any]] = []
    for name, ports in BRIDGED.items():
        container = sh(
            "docker", "ps", "-q", "--filter", f"label=com.docker.compose.service={name}",
            "--filter", "label=com.docker.compose.project=p0-standin",
        ).split()  # fmt: skip
        if not container:
            continue
        info = json.loads(sh("docker", "inspect", container[0]))[0]
        ip = info["NetworkSettings"]["Networks"][P0_NETWORK]["IPAddress"]
        named = [{"name": f"p{p}", "port": p, "protocol": "TCP"} for p in ports]
        objs.append({"apiVersion": "v1", "kind": "Service",
                     "metadata": {"name": name, "namespace": "rag-platform"},
                     "spec": {"ports": [{**n, "targetPort": n["port"]} for n in named]}})  # fmt: skip
        objs.append({"apiVersion": "discovery.k8s.io/v1", "kind": "EndpointSlice",
                     "metadata": {"name": name, "namespace": "rag-platform",
                                  "labels": {"kubernetes.io/service-name": name}},
                     "addressType": "IPv4", "endpoints": [{"addresses": [ip]}], "ports": named})  # fmt: skip
    # e2e only: the stand-in's containers are endpoints, not pods, so the namespace selector in
    # the platform's egress policy cannot match them; allow exactly their subnet and ports
    ports = sorted({p for ps in BRIDGED.values() for p in ps})
    objs.append({"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                 "metadata": {"name": "e2e-egress-stand-in", "namespace": NS},
                 "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/part-of": "agent-platform"}},
                          "policyTypes": ["Egress"],
                          "egress": [{"to": [{"ipBlock": {"cidr": subnet}}],
                                      "ports": [{"protocol": "TCP", "port": p} for p in ports]}]}})  # fmt: skip
    kubectl("apply", "-f", "-", stdin=yaml.safe_dump_all(objs))
    step("stand-in bridged as rag-platform", True, {"services": len(objs) // 2, "subnet": subnet})


def bao(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    resp = httpx.request(
        method, f"{BAO}/v1/{path}", json=body, headers={"X-Vault-Token": BAO_TOKEN}, timeout=30
    )
    if resp.status_code >= 400 and not (resp.status_code == 400 and "already in use" in resp.text):
        raise RuntimeError(f"OpenBao {method} {path}: {resp.status_code} {resp.text[:300]}")
    return resp.json() if resp.content else None


def kv_paths(mount: str, prefix: str = "") -> list[str]:
    listed = httpx.request(
        "LIST",
        f"{BAO}/v1/{mount}/metadata/{prefix}",
        headers={"X-Vault-Token": BAO_TOKEN},
        timeout=30,
    )
    if listed.status_code == 404:
        return []
    out: list[str] = []
    for key in listed.json()["data"]["keys"]:
        out += kv_paths(mount, prefix + key) if key.endswith("/") else [prefix + key]
    return out


def openbao() -> None:
    kubectl("-n", "kube-system", "create", "serviceaccount", "openbao-reviewer")
    kubectl(
        "create", "clusterrolebinding", "openbao-reviewer", "--clusterrole=system:auth-delegator",
        "--serviceaccount=kube-system:openbao-reviewer",
    )  # fmt: skip
    reviewer = kubectl(
        "-n", "kube-system", "create", "token", "openbao-reviewer", "--duration=4h"
    ).strip()
    config = yaml.safe_load(sh("kind", "get", "kubeconfig", "--name", CLUSTER))
    ca = base64.b64decode(config["clusters"][0]["cluster"]["certificate-authority-data"]).decode()
    bao("POST", "sys/auth/kubernetes", {"type": "kubernetes"})
    bao("POST", "auth/kubernetes/config",
        {"kubernetes_host": f"https://{NODE}:6443", "kubernetes_ca_cert": ca, "token_reviewer_jwt": reviewer})  # fmt: skip
    mount = f"agent-platform-{ENV}"
    bao("POST", f"sys/mounts/{mount}", {"type": "kv", "options": {"version": "2"}})
    copied = 0
    for path in kv_paths("agent-platform"):
        data = bao("GET", f"agent-platform/data/{path}")["data"]["data"]
        bao("POST", f"{mount}/data/{path}", {"data": data})
        copied += 1
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    for path, data in {
        "agent-platform/db": {"dsn": DB_IN_CLUSTER},
        "agent-platform/audit": {"dsn": DB_IN_CLUSTER},
        "agent-platform/db-migrate": {"dsn": DB_IN_CLUSTER},
        "agent-platform/checkpoints": {"key": base64.b64encode(os.urandom(32)).decode()},
        "agent-platform/a2a": {"signing_key": pem},
    }.items():
        bao("POST", f"{mount}/data/{path}", {"data": data})
    bao(
        "PUT",
        f"sys/policies/acl/{mount}",
        {"policy": f'path "{mount}/data/*" {{ capabilities = ["read"] }}'},
    )
    bao("POST", f"auth/kubernetes/role/{mount}", {
        "bound_service_account_names": SERVICE_ACCOUNTS, "bound_service_account_namespaces": [NS],
        "audience": "openbao", "token_policies": [mount], "token_ttl": 600, "token_max_ttl": 900,
    })  # fmt: skip
    step("OpenBao Kubernetes auth and the dev mount", True, {"secrets_copied": copied})


# ---------------------------------------------------------------- 4. GitOps


def gitops(commit: str) -> None:
    docs = [d for d in yaml.safe_load_all(build("deploy/argocd")) if d]
    project = next(d for d in docs if d["kind"] == "AppProject")
    app = next(
        d
        for d in docs
        if d["kind"] == "Application" and d["metadata"]["name"] == f"agent-platform-{ENV}"
    )
    images = [
        f"agent-platform/{s}=ghcr.io/deivamanikailasam/agent-platform/{s}@{_digest(s, commit)}"
        for s in ("agent-api", "agent-worker", "mcp-gateway", "a2a-gateway", "ops")
    ]
    app["spec"]["source"]["targetRevision"] = commit
    app["spec"]["source"]["kustomize"] = {"images": images}
    kubectl("apply", "-f", "-", stdin=yaml.safe_dump_all([project, app]))
    deadline, status = time.monotonic() + 900, {}
    while time.monotonic() < deadline:
        got = json.loads(
            kubectl("-n", "argocd", "get", "application", app["metadata"]["name"], "-o", "json")
        )
        status = {
            "sync": got.get("status", {}).get("sync", {}).get("status"),
            "health": got.get("status", {}).get("health", {}).get("status"),
            "revision": got.get("status", {}).get("sync", {}).get("revision"),
        }
        if status["sync"] == "Synced" and status["health"] == "Healthy":
            break
        time.sleep(10)
    else:
        print(kubectl("get", "pods", "-A", "-o", "wide", check=False))
        print(kubectl("-n", NS, "get", "events", "--sort-by=.lastTimestamp", check=False)[-6000:])
        for pod in kubectl("-n", NS, "get", "pods", "-o", "name", check=False).split():
            print(pod, kubectl("-n", NS, "logs", pod, "--all-containers", "--tail=40", check=False))
        print(json.dumps(got.get("status", {}).get("conditions"), indent=1))
        step("dev synced by Argo CD and healthy", False, status)
    migrated = kubectl(
        "-n",
        NS,
        "get",
        "pods",
        "-l",
        "app.kubernetes.io/name=agent-db-migrate",
        "-o",
        "name",
        check=False,
    )
    step(
        "dev synced by Argo CD and healthy",
        True,
        {**status, "images": images, "migration": migrated.strip()},
    )


# ---------------------------------------------------------------- 5. smoke


CLIENT = """
import httpx, json, sys
kc = "http://keycloak.rag-platform.svc:8080/realms/rag-platform/protocol/openid-connect/token"
tok = httpx.post(kc, data={"grant_type": "password", "client_id": "mcp-inspector",
    "username": "alice", "password": "standin-only"}, timeout=30).raise_for_status().json()["access_token"]
h = {"Authorization": f"Bearer {tok}"}
api = "http://agent-api.agent-platform-dev.svc:8091"
run = httpx.post(f"{api}/v1/agents/hello-workflow/runs", json={"input": {"name": "kind"}}, headers=h, timeout=60)
run.raise_for_status()
rid = run.json()["run_id"]
events = httpx.get(f"{api}/v1/runs/{rid}/events", headers=h, timeout=120).text
done = [json.loads(l[6:]) for l in events.splitlines() if l.startswith("data: ")]
print(json.dumps({"run_id": rid, "last": done[-1]["type"], "status": done[-1].get("outcome", {})}))
sys.exit(0 if done and done[-1]["type"] == "RUN_FINISHED" else 1)
"""
PROBE = """
import socket, sys
try:
    socket.create_connection(("agent-api.agent-platform-dev.svc", 8091), 5)
except OSError as e:
    print("refused:", type(e).__name__); sys.exit(0)
print("reached agent-api"); sys.exit(1)
"""


def client_pod(namespace: str, name: str, image: str, code: str) -> tuple[bool, str]:
    pod = {
        "apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": namespace},
        "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False,
                 "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
                 "containers": [{"name": "client", "image": image, "command": ["python", "-c", code],
                                 "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                                     "capabilities": {"drop": ["ALL"]}}}]},
    }  # fmt: skip
    kubectl("apply", "-f", "-", stdin=yaml.safe_dump(pod))
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        phase = kubectl(
            "-n", namespace, "get", "pod", name, "-o", "jsonpath={.status.phase}", check=False
        )
        if phase in ("Succeeded", "Failed"):
            return phase == "Succeeded", kubectl("-n", namespace, "logs", name, check=False).strip()
        time.sleep(3)
    return False, "timed out"


def smoke(commit: str) -> None:
    image = f"ghcr.io/deivamanikailasam/agent-platform/agent-api@{_digest('agent-api', commit)}"
    restricted = {"pod-security.kubernetes.io/enforce": "restricted"}
    kubectl("apply", "-f", "-", stdin=yaml.safe_dump_all([
        {"apiVersion": "v1", "kind": "Namespace",
         "metadata": {"name": "e2e-client", "labels": {**restricted, "agent-platform/ingress": "true"}}},
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "e2e-outsider", "labels": restricted}},
    ]))  # fmt: skip
    ok, out = client_pod("e2e-client", "run-hello", image, CLIENT)
    step("a run through agent-api in the cluster", ok, out)
    ok, out = client_pod("e2e-outsider", "probe", image, PROBE)
    step("a namespace the ingress policy does not admit cannot reach agent-api", ok, out)


def wait_for_images(commit: str, timeout: float = 1800) -> None:
    """The images workflow builds this commit's images in parallel; wait until all are pushed."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            for s in ("agent-api", "agent-worker", "mcp-gateway", "a2a-gateway", "ops"):
                _digest(s, commit)
            return
        except Exception:  # not pushed yet
            if time.monotonic() > deadline:
                raise
            time.sleep(30)


def main() -> int:
    commit = os.environ.get("E2E_COMMIT") or sh("git", "-C", str(ROOT), "rev-parse", "HEAD").strip()
    results["commit"] = commit
    ok = True
    try:
        wait_for_images(commit)
        cluster()
        admission()
        bridge()
        openbao()
        gitops(commit)
        smoke(commit)
    except Exception as exc:  # record what failed, then fail
        ok = False
        results["error"] = f"{type(exc).__name__}: {exc}"[-3000:]
        print(f"kind-e2e: {results['error']}", file=sys.stderr)
    results["passed"] = ok
    out = ROOT / "evals" / "results" / "deploy"
    out.mkdir(parents=True, exist_ok=True)
    (out / "kind-e2e.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"kind-e2e: {'PASSED' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
