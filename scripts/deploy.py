"""Deployment checks and image pins (Step 38).

deploy check                       # every overlay renders, validates against Kubernetes 1.37
                                   # schemas, and meets deploy_policy; Argo CD apps validate too
deploy images <env> <commit>       # pin the overlay's images to the digests the images workflow
                                   # pushed for that commit (read from GHCR)
deploy render <env>                # print an overlay as applied

Needs kustomize 5.8.2 and kubeconform 0.8.0 on PATH (versions pinned in ci.yml).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from agent_governance.deploy_policy import ENVIRONMENTS, problems

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
REGISTRY = "ghcr.io/deivamanikailasam/agent-platform"
SERVICES = ("agent-api", "agent-worker", "mcp-gateway", "a2a-gateway", "ops")
KUBERNETES = "1.37.0"
# schemas pinned to commits, so validation does not change under us
SCHEMAS = (
    "https://raw.githubusercontent.com/yannh/kubernetes-json-schema/"
    "aff1ca44984cbbcfa376c1cb6fdc4bb6aa0381fd/"
    "{{.NormalizedKubernetesVersion}}-standalone{{.StrictSuffix}}/{{.ResourceKind}}{{.KindSuffix}}.json",
    "https://raw.githubusercontent.com/datreeio/CRDs-catalog/"
    "63669a570e231d4f1f8396d229a1de512bcf0a34/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json",
)


def build(path: Path) -> str:
    return subprocess.run(  # noqa: S603 - fixed tool, repo paths
        ["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone", str(path)],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def validate(rendered: str, label: str, skip: str = "") -> list[str]:
    args = ["kubeconform", "-strict", "-summary", "-kubernetes-version", KUBERNETES]
    if skip:
        args += ["-skip", skip]
    for location in SCHEMAS:
        args += ["-schema-location", location]
    proc = subprocess.run(args, input=rendered, capture_output=True, text=True)  # noqa: S603
    print(f"deploy-check: {label}: {proc.stdout.strip().splitlines()[-1] if proc.stdout else ''}")
    if proc.returncode == 0:
        return []
    return [
        f"{label}: {line}"
        for line in proc.stdout.splitlines()
        if "failed" in line or "error" in line.lower()
    ] or [f"{label}: kubeconform failed: {proc.stderr.strip()}"]


def check() -> int:
    for tool in ("kustomize", "kubeconform"):
        if shutil.which(tool) is None:
            print(
                f"deploy-check: {tool} is not installed (see ci.yml for the pinned version)",
                file=sys.stderr,
            )
            return 1
    found: list[str] = []
    for env in ENVIRONMENTS:
        rendered = build(DEPLOY / "overlays" / env)
        found += [f"{env}: {p}" for p in problems(env, list(yaml.safe_load_all(rendered)))]
        found += validate(rendered, env)
    found += validate(build(DEPLOY / "argocd"), "argocd")
    for env in ("staging", "prod"):
        # kubeconform 0.8.0 finds no schema for External Secrets' SecretStore v1 although the
        # catalogue has one; it is reported as skipped rather than passed
        found += validate(build(DEPLOY / "temporal" / env), f"temporal-{env}", skip="SecretStore")
    for p in found:
        print(f"deploy-check: {p}", file=sys.stderr)
    print(f"deploy-check: {len(ENVIRONMENTS)} environments, {len(found)} problem(s)")
    return 1 if found else 0


def _digest(image: str, tag: str) -> str:
    repo = f"{REGISTRY.removeprefix('ghcr.io/')}/{image}"
    token = os.environ.get("GHCR_TOKEN")
    if not token:  # anonymous pull token (public packages)
        with urllib.request.urlopen(f"https://ghcr.io/token?scope=repository:{repo}:pull") as r:
            token = json.load(r)["token"]
    req = urllib.request.Request(
        f"https://ghcr.io/v2/{repo}/manifests/{tag}",
        method="HEAD",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.oci.image.index.v1+json, "
            "application/vnd.oci.image.manifest.v1+json",
        },
    )
    with urllib.request.urlopen(req) as r:  # noqa: S310
        return str(r.headers["Docker-Content-Digest"])


def set_images(env: str, commit: str) -> int:
    path = DEPLOY / "overlays" / env / "kustomization.yaml"
    text = path.read_text()
    images: list[dict[str, Any]] = [
        {"name": f"agent-platform/{s}", "newName": f"{REGISTRY}/{s}", "digest": _digest(s, commit)}
        for s in SERVICES
    ]
    block = (
        f"# images from commit {commit} (images workflow; signed, scanned)\nimages:\n"
        + "".join(
            f'  - {{ name: {i["name"]}, newName: {i["newName"]}, digest: "{i["digest"]}" }}\n'
            for i in images
        )
    )
    head = text.split("\n# images from commit")[0].split("\nimages:")[0].rstrip("\n")
    path.write_text(f"{head}\n{block}")
    print(f"deploy: {env} pinned to {commit}")
    return 0


def main(argv: list[str]) -> int:
    match argv:
        case ["check"]:
            return check()
        case ["images", env, commit] if env in ENVIRONMENTS:
            return set_images(env, commit)
        case ["render", env] if env in ENVIRONMENTS:
            print(build(DEPLOY / "overlays" / env), end="")
            return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
