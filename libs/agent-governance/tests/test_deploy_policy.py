"""Step 38: every rendered environment meets the deployment rules, and each rule catches what it
is for (a bad manifest is refused with a reason)."""

from __future__ import annotations

import copy
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from agent_governance.deploy_policy import problems

ROOT = Path(__file__).resolve().parents[3]
DIGEST = "@sha256:" + "a" * 64


def pod(name: str = "agent-api", **container: Any) -> dict[str, Any]:
    return {
        "automountServiceAccountToken": False,
        "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
        "containers": [
            {
                "name": name,
                "image": f"ghcr.io/x/{name}{DIGEST}",
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                },
                "resources": {"limits": {"memory": "512Mi"}},
                **container,
            }
        ],
    }


def deployment(name: str, replicas: int = 3) -> dict[str, Any]:
    return {
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": "agent-platform-prod"},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "template": {"spec": pod(name)},
        },
    }


def good_prod() -> list[dict[str, Any]]:
    names = ("agent-api", "mcp-gateway", "a2a-gateway", "agent-worker")
    return [
        {
            "kind": "Namespace",
            "metadata": {
                "name": "agent-platform-prod",
                "labels": {"pod-security.kubernetes.io/enforce": "restricted"},
            },
        },
        {
            "kind": "NetworkPolicy",
            "metadata": {"name": "default-deny"},
            "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
        },
        {
            "kind": "ConfigMap",
            "metadata": {"name": "agent-platform-env-abc"},
            "data": {
                "AGENT_ENV": "prod",
                "AGENT_BAO_K8S_ROLE": "agent-platform-prod",
                "AGENT_BAO_KV_MOUNT": "agent-platform-prod",
                "AGENT_PLATFORM_CLIENT_ID": "agent-platform-prod",
                "AGENT_DB_DSN": "${secret:agent-platform/db#dsn}",
            },
        },
        *[deployment(n) for n in names],
        *[
            {
                "kind": "PodDisruptionBudget",
                "metadata": {"name": n},
                "spec": {"selector": {"matchLabels": {"app.kubernetes.io/name": n}}},
            }
            for n in names
        ],
        {
            "kind": "ScaledObject",
            "metadata": {"name": "agent-worker"},
            "spec": {"scaleTargetRef": {"name": "agent-worker"}, "minReplicaCount": 2},
        },
    ]


def by(objs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    return next(o for o in objs if o["kind"] == kind and o["metadata"]["name"].startswith(name))


def test_a_compliant_environment_has_no_problems() -> None:
    assert problems("prod", good_prod()) == []


def test_every_rule_catches_its_violation() -> None:
    objs = copy.deepcopy(good_prod())
    by(objs, "Namespace", "agent")["metadata"]["labels"] = {}
    objs.append({"kind": "Secret", "metadata": {"name": "db"}})
    objs.append({"kind": "Service", "metadata": {"name": "api"}, "spec": {"type": "NodePort"}})
    objs = [o for o in objs if o["kind"] != "NetworkPolicy"]
    env = by(objs, "ConfigMap", "agent-platform-env")["data"]
    env["AGENT_DB_DSN"] = "postgresql://agent:hunter2@db/agent"
    env["AGENT_BAO_KV_MOUNT"] = "agent-platform-staging"
    api = by(objs, "Deployment", "agent-api")
    api["spec"]["replicas"] = 1
    c = api["spec"]["template"]["spec"]["containers"][0]
    c["image"] = "ghcr.io/x/agent-api:latest"
    c["securityContext"] = {"privileged": True, "capabilities": {"add": ["NET_ADMIN"]}}
    c["resources"] = {}
    c["env"] = [{"name": "LANGFUSE_SECRET_KEY", "value": "sk-live"}]
    spec = by(objs, "Deployment", "mcp-gateway")["spec"]["template"]["spec"]
    spec["automountServiceAccountToken"] = True
    spec["hostNetwork"] = True
    spec["volumes"] = [{"name": "h", "hostPath": {"path": "/"}}]
    objs = [
        o
        for o in objs
        if not (o["kind"] == "PodDisruptionBudget" and o["metadata"]["name"] == "a2a-gateway")
    ]
    by(objs, "ScaledObject", "agent-worker")["spec"]["minReplicaCount"] = 0
    found = problems("prod", objs)
    expected = [
        "Namespace/agent-platform-prod: Pod Security 'restricted' is not enforced",
        "Secret objects are not allowed",
        "Service/api: type NodePort exposes it outside the cluster",
        "no deny-by-default NetworkPolicy",
        "config AGENT_DB_DSN holds a secret value",
        "config AGENT_BAO_KV_MOUNT='agent-platform-staging' does not name the prod environment",
        "Deployment/agent-api: 1 replica(s), prod needs 3",
        "Deployment/agent-api/agent-api: image ghcr.io/x/agent-api:latest is not pinned by digest",
        "Deployment/agent-api/agent-api: root filesystem is writable",
        "Deployment/agent-api/agent-api: allows privilege escalation",
        "Deployment/agent-api/agent-api: is privileged",
        "Deployment/agent-api/agent-api: does not drop all capabilities",
        "Deployment/agent-api/agent-api: adds capabilities",
        "Deployment/agent-api/agent-api: no memory limit",
        "Deployment/agent-api/agent-api: env LANGFUSE_SECRET_KEY holds a secret value",
        "Deployment/mcp-gateway: mounts a Kubernetes API token",
        "Deployment/mcp-gateway: uses hostNetwork",
        "Deployment/mcp-gateway: mounts a hostPath volume",
        "Deployment/a2a-gateway: no PodDisruptionBudget",
        "agent-worker scales down below 2 in prod",
    ]
    missing = [e for e in expected if not any(p.startswith(e) for p in found)]
    assert missing == [], found


def test_a_root_container_without_seccomp_is_refused() -> None:
    objs = good_prod()
    spec = by(objs, "Deployment", "agent-worker")["spec"]["template"]["spec"]
    spec["securityContext"] = {}
    found = problems("prod", objs)
    assert "Deployment/agent-worker/agent-worker: may run as root (runAsNonRoot)" in found
    assert "Deployment/agent-worker/agent-worker: no seccomp profile" in found


def test_dev_allows_one_replica_and_no_autoscaling() -> None:
    objs = [o for o in good_prod() if o["kind"] not in ("ScaledObject", "PodDisruptionBudget")]
    for o in objs:
        o["metadata"]["name"] = o["metadata"]["name"].replace("prod", "dev")
        if o["kind"] == "Deployment":
            o["spec"]["replicas"] = 1
            o["metadata"]["namespace"] = "agent-platform-dev"
        if o["kind"] == "ConfigMap":
            o["data"] = {k: v.replace("prod", "dev") for k, v in o["data"].items()}
    assert problems("dev", objs) == []


@pytest.mark.skipif(shutil.which("kustomize") is None, reason="kustomize not installed")
@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_the_repository_overlays_meet_the_rules(env: str) -> None:
    rendered = subprocess.run(
        [
            "kustomize",
            "build",
            "--load-restrictor",
            "LoadRestrictionsNone",
            f"deploy/overlays/{env}",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert problems(env, list(yaml.safe_load_all(rendered))) == []
