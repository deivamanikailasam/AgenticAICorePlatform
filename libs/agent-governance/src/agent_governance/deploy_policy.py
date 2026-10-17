"""Rules every rendered environment must satisfy before it can be deployed (Step 38).

``problems(env, objects)`` takes the objects ``kustomize build`` produced for one overlay and
returns what is wrong, in words. The rules are the spec's security defaults applied to
Kubernetes:

* images pinned by digest;
* Pod Security "restricted" enforced on the namespace and met by every pod: non-root, read-only
  root filesystem, no privilege escalation, all capabilities dropped, the runtime's seccomp
  profile, no host namespaces or host paths;
* no Kubernetes API token mounted; no Secret objects and no secret values in config (they are
  ``${secret:...}`` references, resolved from OpenBao);
* nothing exposed outside the cluster (no NodePort or LoadBalancer Services);
* deny-by-default network policy;
* memory limits on every container;
* staging and prod: at least two replicas of each stateless service, a disruption budget for
  every Deployment, and workers that autoscale with at least ``MIN_WORKERS[env]``;
* environments kept apart: namespace, ``AGENT_ENV``, OpenBao role and mount, and Keycloak client
  all name the environment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

ENVIRONMENTS = ("dev", "staging", "prod")
STATELESS = ("agent-api", "mcp-gateway", "a2a-gateway")
MIN_REPLICAS = {"dev": 1, "staging": 2, "prod": 3}
MIN_WORKERS = {"staging": 1, "prod": 2}
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
SECRET_REF = re.compile(r"^\$\{secret:[A-Za-z0-9_./-]+#[A-Za-z0-9_.-]+\}$")
# config keys whose values must be references, never literal values
SECRET_KEYS = re.compile(r"(PASSWORD|SECRET|TOKEN|_KEY$|DSN$)")
# literal credentials that must never appear in config: user:password@ in a URL
INLINE_PASSWORD = re.compile(r"://[^/@\s:]+:[^/@\s]+@")
POD_KINDS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")
SEPARATED = ("AGENT_ENV", "AGENT_BAO_K8S_ROLE", "AGENT_BAO_KV_MOUNT", "AGENT_PLATFORM_CLIENT_ID")


def _name(obj: Mapping[str, Any]) -> str:
    return f"{obj.get('kind')}/{obj.get('metadata', {}).get('name')}"


def _pod_spec(obj: Mapping[str, Any]) -> Mapping[str, Any] | None:
    spec = obj.get("spec") or {}
    if obj.get("kind") == "CronJob":
        spec = spec.get("jobTemplate", {}).get("spec", {})
    return (spec.get("template") or {}).get("spec") if obj.get("kind") in POD_KINDS else None


def _pod_problems(where: str, pod: Mapping[str, Any]) -> list[str]:
    out = []
    sc = pod.get("securityContext") or {}
    if pod.get("automountServiceAccountToken") is not False:
        out.append(
            f"{where}: mounts a Kubernetes API token (set automountServiceAccountToken: false)"
        )
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        if pod.get(flag):
            out.append(f"{where}: uses {flag}")
    if any("hostPath" in v for v in pod.get("volumes") or []):
        out.append(f"{where}: mounts a hostPath volume")
    for c in [*(pod.get("initContainers") or []), *(pod.get("containers") or [])]:
        cname = f"{where}/{c.get('name')}"
        csc = c.get("securityContext") or {}
        if not DIGEST.search(str(c.get("image", ""))):
            out.append(f"{cname}: image {c.get('image')} is not pinned by digest")
        if csc.get("runAsNonRoot", sc.get("runAsNonRoot")) is not True:
            out.append(f"{cname}: may run as root (runAsNonRoot)")
        if csc.get("readOnlyRootFilesystem") is not True:
            out.append(f"{cname}: root filesystem is writable")
        if csc.get("allowPrivilegeEscalation") is not False:
            out.append(f"{cname}: allows privilege escalation")
        if csc.get("privileged"):
            out.append(f"{cname}: is privileged")
        if "ALL" not in ((csc.get("capabilities") or {}).get("drop") or []):
            out.append(f"{cname}: does not drop all capabilities")
        if (csc.get("capabilities") or {}).get("add"):
            out.append(f"{cname}: adds capabilities")
        seccomp = (csc.get("seccompProfile") or sc.get("seccompProfile") or {}).get("type")
        if seccomp not in ("RuntimeDefault", "Localhost"):
            out.append(f"{cname}: no seccomp profile")
        if not ((c.get("resources") or {}).get("limits") or {}).get("memory"):
            out.append(f"{cname}: no memory limit")
        for e in c.get("env") or []:
            if "value" in e and _secret_value(e["name"], str(e["value"])):
                out.append(f"{cname}: env {e['name']} holds a secret value, not a reference")
    return out


def _secret_value(key: str, value: str) -> bool:
    if INLINE_PASSWORD.search(value):
        return True
    return bool(SECRET_KEYS.search(key) and value and not SECRET_REF.match(value))


def problems(
    env: str, objects: Iterable[Mapping[str, Any]], prefix: str = "agent-platform"
) -> list[str]:
    """``prefix`` names the environments: ``agent-platform-<env>`` for the platform, the
    project's own name for an agent project (Step 39)."""
    objs = [o for o in objects if o]
    out: list[str] = []
    by_kind: dict[str, list[Mapping[str, Any]]] = {}
    for o in objs:
        by_kind.setdefault(str(o.get("kind")), []).append(o)
    if env not in ENVIRONMENTS:
        return [f"unknown environment {env!r}"]
    namespaces = by_kind.get("Namespace", [])
    expected_ns = f"{prefix}-{env}"
    if [n["metadata"]["name"] for n in namespaces] != [expected_ns]:
        out.append(f"the overlay must create exactly the namespace {expected_ns}")
    for ns in namespaces:
        if (ns["metadata"].get("labels") or {}).get("pod-security.kubernetes.io/enforce") != (
            "restricted"
        ):
            out.append(f"{_name(ns)}: Pod Security 'restricted' is not enforced")
    for o in objs:
        where = (o.get("metadata") or {}).get("namespace")
        if o.get("kind") != "Namespace" and where not in (None, expected_ns):
            out.append(f"{_name(o)}: in namespace {where}, not {expected_ns}")
    if by_kind.get("Secret"):
        out.append("Secret objects are not allowed: secrets come from OpenBao at run time")
    for svc in by_kind.get("Service", []):
        kind = (svc.get("spec") or {}).get("type", "ClusterIP")
        if kind in ("NodePort", "LoadBalancer"):
            out.append(f"{_name(svc)}: type {kind} exposes it outside the cluster")
    policies = by_kind.get("NetworkPolicy", [])
    if not any(
        (p["spec"].get("podSelector") or {}) == {}
        and set(p["spec"].get("policyTypes") or []) == {"Ingress", "Egress"}
        and not p["spec"].get("ingress")
        and not p["spec"].get("egress")
        for p in policies
    ):
        out.append("no deny-by-default NetworkPolicy")
    for o in objs:
        pod = _pod_spec(o)
        if pod is not None:
            out.extend(_pod_problems(_name(o), pod))
    config: dict[str, str] = {}
    for cm in by_kind.get("ConfigMap", []):
        if cm["metadata"]["name"].startswith("agent-platform-env"):
            config.update({k: str(v) for k, v in (cm.get("data") or {}).items()})
    if not config:
        out.append("no agent-platform-env ConfigMap")
    for key, value in config.items():
        if _secret_value(key, value):
            out.append(f"config {key} holds a secret value, not a ${{secret:...}} reference")
    if config.get("AGENT_ENV") != env:
        out.append(f"config AGENT_ENV is {config.get('AGENT_ENV')!r}, not {env!r}")
    for key in SEPARATED[1:]:
        if env not in config.get(key, ""):
            out.append(f"config {key}={config.get(key, '')!r} does not name the {env} environment")
    deployments = {d["metadata"]["name"]: d for d in by_kind.get("Deployment", [])}
    budgets = {
        tuple(sorted(((p["spec"].get("selector") or {}).get("matchLabels") or {}).items()))
        for p in by_kind.get("PodDisruptionBudget", [])
    }
    for name in STATELESS:
        d = deployments.get(name)
        if d is None:
            out.append(f"Deployment/{name} is missing")
            continue
        if int(d["spec"].get("replicas", 1)) < MIN_REPLICAS[env]:
            out.append(
                f"Deployment/{name}: {d['spec'].get('replicas', 1)} replica(s), {env} needs {MIN_REPLICAS[env]}"
            )
    if env in MIN_WORKERS:
        for name, d in deployments.items():
            labels = tuple(sorted((d["spec"]["selector"].get("matchLabels") or {}).items()))
            if labels not in budgets:
                out.append(f"Deployment/{name}: no PodDisruptionBudget")
        scaled = {s["spec"]["scaleTargetRef"]["name"]: s for s in by_kind.get("ScaledObject", [])}
        worker = scaled.get("agent-worker")
        if worker is None:
            out.append("agent-worker does not autoscale (no ScaledObject)")
        elif int(worker["spec"].get("minReplicaCount", 0)) < MIN_WORKERS[env]:
            out.append(f"agent-worker scales down below {MIN_WORKERS[env]} in {env}")
    return out
