"""Enforce the container security defaults on every compose file in the repo.

Rules (from the spec's conventions): every published port binds to 127.0.0.1, every image is
pinned by digest, every long-running service has a memory limit, and no service mounts the
Docker socket or runs privileged.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_GLOBS = ("infra/compose/*.yaml", "tests/stack/*.yaml")


def _port_ok(port: Any) -> bool:
    if isinstance(port, dict):
        return str(port.get("host_ip", "")) == "127.0.0.1" or "published" not in port
    text = str(port)
    parts = text.split(":")
    return len(parts) < 2 or parts[0] == "127.0.0.1"


def _check_dockerfile(compose_path: Path, build: Any, where: str) -> list[str]:
    """Locally built images are fine if every FROM line in their Dockerfile is digest-pinned."""
    if isinstance(build, str):
        build = {"context": build}
    context = (compose_path.parent / build.get("context", ".")).resolve()
    dockerfile = context / build.get("dockerfile", "Dockerfile")
    if not dockerfile.exists():
        return [f"{where}: Dockerfile {dockerfile} not found"]
    problems = []
    for line in dockerfile.read_text().splitlines():
        words = line.split()
        if words and words[0].upper() == "FROM" and "@sha256:" not in line:
            base = words[1] if len(words) > 1 else "?"
            problems.append(f"{where}: base image '{base}' is not pinned by digest")
    return problems


def check_file(path: Path) -> list[str]:
    doc = yaml.safe_load(path.read_text()) or {}
    problems: list[str] = []
    for name, svc in (doc.get("services") or {}).items():
        where = f"{path.relative_to(ROOT)}:{name}"
        image = svc.get("image")
        build = svc.get("build")
        if build:
            problems.extend(_check_dockerfile(path, build, where))
        elif image and "@sha256:" not in str(image):
            problems.append(f"{where}: image '{image}' is not pinned by digest")
        if not image and not build:
            problems.append(f"{where}: has neither image nor build")
        for port in svc.get("ports") or []:
            if not _port_ok(port):
                problems.append(f"{where}: port '{port}' must bind to 127.0.0.1")
        if svc.get("privileged"):
            problems.append(f"{where}: privileged containers are not allowed")
        for vol in svc.get("volumes") or []:
            if "docker.sock" in str(vol):
                problems.append(f"{where}: mounting the Docker socket is not allowed")
        one_shot = svc.get("restart") == "no" or svc.get("profiles")
        limits = (svc.get("deploy") or {}).get("resources", {}).get("limits", {})
        if not one_shot and not (svc.get("mem_limit") or limits.get("memory")):
            problems.append(f"{where}: long-running service needs a memory limit")
    return problems


def main(paths: list[str]) -> int:
    files = [Path(p).resolve() for p in paths] or [
        f for g in COMPOSE_GLOBS for f in sorted(ROOT.glob(g))
    ]
    problems = [p for f in files for p in check_file(f)]
    for p in problems:
        print(f"compose-check: {p}")
    print(f"compose-check: {len(files)} file(s), {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
