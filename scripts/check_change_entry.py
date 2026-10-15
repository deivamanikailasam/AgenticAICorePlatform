"""Every commit that changes code or config must add a plain-English entry in docs/changes/.

Usage: ``check_change_entry.py [<base>..<head>]`` (default ``HEAD~1..HEAD``). In CI the range is
the pushed commits, checked one commit at a time.
"""

from __future__ import annotations

import re
import subprocess
import sys

ENTRY = re.compile(r"^docs/changes/\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$")
REQUIRED_HEADINGS = ("## What changed", "## Why", "## How to check it", "## Risks and follow-ups")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, check=True, text=True).stdout  # noqa: S603, S607


def check_commit(sha: str) -> list[str]:
    files = [f for f in git("show", "--name-only", "--format=", sha).splitlines() if f]
    entries = [f for f in files if ENTRY.match(f)]
    problems: list[str] = []
    if not entries:
        problems.append(f"{sha[:8]}: no docs/changes/YYYY-MM-DD-<slug>.md entry")
    for entry in entries:
        try:
            body = git("show", f"{sha}:{entry}")
        except subprocess.CalledProcessError:
            continue  # entry deleted in this commit
        missing = [h for h in REQUIRED_HEADINGS if h not in body]
        if missing:
            problems.append(f"{sha[:8]}: {entry} is missing {', '.join(missing)}")
    return problems


def main(argv: list[str]) -> int:
    rng = argv[0] if argv else "HEAD~1..HEAD"
    shas = git("rev-list", "--no-merges", rng).split()
    problems = [p for s in shas for p in check_commit(s)]
    for p in problems:
        print(f"change-entry: {p}")
    print(f"change-entry: {len(shas)} commit(s), {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
