"""Check a change against the promotion rules (Step 38), base commit against head.

promotion_check --base REF     # REF..HEAD (CI: the pull request's base, or the push's previous commit)
promotion_check                # the last commit against the working tree
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml
from agent_governance.promotion import ENVS, Policy, State, problems, tree_hash

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(ROOT), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _read(ref: str | None, path: str) -> bytes | None:
    if ref is None:
        file = ROOT / path
        return file.read_bytes() if file.is_file() else None
    try:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(ROOT), "show", f"{ref}:{path}"],  # noqa: S607
            check=True,
            capture_output=True,
        ).stdout
    except subprocess.CalledProcessError:
        return None


def _files(ref: str | None, prefix: str) -> list[str]:
    if ref is None:
        base = ROOT / prefix
        return (
            sorted(str(p.relative_to(ROOT)) for p in base.rglob("*.yaml")) if base.exists() else []
        )
    out = _git("ls-tree", "-r", "--name-only", ref, "--", prefix)
    return sorted(p for p in out.split() if p.endswith(".yaml"))


def _version_files(ref: str | None, files: list[str], root: str) -> dict[str, bytes]:
    """agent.yaml and workers/*.yaml under ``root``, keyed relative to it."""
    out = {}
    for path in files:
        rel = path.removeprefix(root + "/")
        if rel == "agent.yaml" or (rel.startswith("workers/") and rel.count("/") == 1):
            data = _read(ref, path)
            if data is not None:
                out[rel] = data
    return out


def state_at(ref: str | None) -> State:
    """The deployment state at a commit (None: the working tree)."""
    state = State()
    for env in ENVS:
        data = _read(ref, f"deploy/releases/{env}.yaml")
        state.releases[env] = dict((yaml.safe_load(data) or {}).get("agents") or {}) if data else {}
    snaps = _files(ref, "deploy/specs")
    for agent, version in sorted({tuple(p.split("/")[2:4]) for p in snaps if p.count("/") >= 4}):
        root = f"deploy/specs/{agent}/{version}"
        state.snapshots[(agent, version)] = tree_hash(
            _version_files(ref, [p for p in snaps if p.startswith(root + "/")], root)
        )
    dev = _files(ref, "agents")
    for agent in sorted({p.split("/")[1] for p in dev if p.count("/") >= 2}):
        root = f"agents/{agent}"
        files = _version_files(ref, [p for p in dev if p.startswith(root + "/")], root)
        if "agent.yaml" in files:
            spec = yaml.safe_load(files["agent.yaml"]) or {}
            state.dev[agent] = (str(spec.get("version")), tree_hash(files))
    return state


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="promotion_check")
    p.add_argument("--base", help="compare REF..HEAD (default: HEAD against the working tree)")
    args = p.parse_args(argv)
    policy = Policy.of(yaml.safe_load((ROOT / "deploy" / "promotion.yaml").read_text()))
    if args.base:
        base, head = state_at(args.base), state_at("HEAD")
    else:
        base, head = state_at("HEAD"), state_at(None)
    found = problems(base, head, policy)
    for line in found:
        print(f"promotion-check: {line}", file=sys.stderr)
    released = sum(len(v) for v in head.releases.values())
    print(
        f"promotion-check: {len(head.snapshots)} snapshots, {released} releases,"
        f" {len(found)} problem(s)"
    )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
