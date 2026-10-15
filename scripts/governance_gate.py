"""The high-risk merge gate (Step 32), run by CI on every pull request and review.

A pull request that changes a spec which is high-risk before or after the change, or a governance
file (``governance/approvers.yaml`` lists them), fails this check until one of the ``high``
approvers in ``governance/approvers.yaml`` has approved its latest commit. Made a required status
check on ``main``, it means such a change cannot merge without the named approver.

The approvers are read from the base branch, so a pull request can't name its own approver.

    python -m scripts.governance_gate          # in GitHub Actions (pull_request, pull_request_review)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import httpx
import yaml
from agent_governance.codeowners import Review, decide


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout  # noqa: S603, S607


def show(sha: str, path: str) -> str | None:
    try:
        return git("show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        return None  # added or deleted on one side


def reviews(repo: str, number: int, token: str) -> list[Review]:
    out: list[Review] = []
    url: str | None = f"https://api.github.com/repos/{repo}/pulls/{number}/reviews?per_page=100"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    while url:
        resp = httpx.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        out += [
            Review(r["user"]["login"], r["state"], r.get("commit_id") or "") for r in resp.json()
        ]
        url = resp.links.get("next", {}).get("url")
    return out


def main() -> int:
    event: dict[str, Any] = json.loads(open(os.environ["GITHUB_EVENT_PATH"]).read())  # noqa: SIM115
    pr = event["pull_request"]
    base, head = pr["base"]["sha"], pr["head"]["sha"]
    approvers = yaml.safe_load(show(base, "governance/approvers.yaml") or "") or {}
    paths = git("diff", "--name-only", f"{base}...{head}").split()
    changed = [(p, show(base, p), show(head, p)) for p in paths]
    found = reviews(os.environ["GITHUB_REPOSITORY"], pr["number"], os.environ["GITHUB_TOKEN"])
    d = decide(changed, found, head, pr["user"]["login"], approvers)
    for need in d.needs:
        print(f"needs a high-risk approver: {need}")
    print(("PASS: " if d.ok else "FAIL: ") + d.reason)
    if d.approved_by:
        print(f"approved by: {', '.join(d.approved_by)}")
    return 0 if d.ok else 1


if __name__ == "__main__":
    sys.exit(main())
