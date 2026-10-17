"""Check a change against the promotion rules (Step 38), base commit against head.

promotion_check --base REF     # REF..HEAD (CI: the pull request's base, or the push's previous commit)
promotion_check                # the last commit against the working tree
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_governance.promotion import State
from agent_governance.release_repo import ReleaseRepo

ROOT = Path(__file__).resolve().parents[1]


def state_at(ref: str | None) -> State:
    """The deployment state at a commit (None: the working tree)."""
    return ReleaseRepo(ROOT).state_at(ref)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="promotion_check")
    p.add_argument("--base", help="compare REF..HEAD (default: HEAD against the working tree)")
    args = p.parse_args(argv)
    repo = ReleaseRepo(ROOT)
    base, head = (args.base, "HEAD") if args.base else ("HEAD", None)
    found = repo.check(base, head)
    for line in found:
        print(f"promotion-check: {line}", file=sys.stderr)
    state = repo.state_at(head)
    released = sum(len(v) for v in state.releases.values())
    print(
        f"promotion-check: {len(state.snapshots)} snapshots, {released} releases,"
        f" {len(found)} problem(s)"
    )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
