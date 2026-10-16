"""Red-team findings as GitHub issues (Step 35).

    redteam_issues sync REPORT [REPORT ...] [--dry-run]

One issue per weakness, matched by the finding's fingerprint (a marker in the issue body), so the
same attack failing again updates its issue instead of opening another:

* a new finding opens an issue labelled ``redteam``, its ASI category and ``severity:<level>``;
* a finding whose issue is closed reopens it; an open one gets a comment for this run;
* an attack that held this run closes its open issue (fixed and re-verified); an attack that
  didn't run leaves its issue as it is.

Needs ``GITHUB_TOKEN`` (issues: write) and ``GITHUB_REPOSITORY``; ``--dry-run`` only prints the
plan. ``open_blocking`` lists open high/critical red-team issues for the release gate.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from agent_evals.redteam import BLOCKING, Report, Result, merge

API = "https://api.github.com"
MARKER = re.compile(r"<!-- redteam-fingerprint: ([0-9a-f]{16}) -->")


@dataclass(frozen=True)
class Action:
    kind: str  # open | reopen | comment | close
    fingerprint: str
    title: str
    number: int | None = None


class GitHub:
    def __init__(self, repo: str, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self.repo = repo
        self.client = httpx.Client(
            base_url=API,
            transport=transport,
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def issues(self, state: str = "all") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = self.client.get(
                f"/repos/{self.repo}/issues",
                params={"labels": "redteam", "state": state, "per_page": 100, "page": page},
            )
            resp.raise_for_status()
            batch = [i for i in resp.json() if "pull_request" not in i]
            out += batch
            if len(resp.json()) < 100:
                return out
            page += 1

    def create(self, title: str, body: str, labels: list[str]) -> int:
        resp = self.client.post(
            f"/repos/{self.repo}/issues", json={"title": title, "body": body, "labels": labels}
        )
        resp.raise_for_status()
        return int(resp.json()["number"])

    def comment(self, number: int, body: str) -> None:
        self.client.post(
            f"/repos/{self.repo}/issues/{number}/comments", json={"body": body}
        ).raise_for_status()

    def set_state(self, number: int, state: str) -> None:
        body: dict[str, Any] = {"state": state}
        if state == "closed":
            body["state_reason"] = "completed"
        self.client.patch(f"/repos/{self.repo}/issues/{number}", json=body).raise_for_status()


def title_of(r: Result) -> str:
    return f"red team [{r.severity}] {r.asi}: {r.title}"[:250]


def body_of(r: Result, report: Report) -> str:
    return (
        f"**Attack:** `{r.id}` ({r.source})  \n**Category:** {r.asi}  \n"
        f"**Severity:** {r.severity}{' (blocks a release)' if r.severity in BLOCKING else ''}\n\n"
        f"**What happened:** {r.detail}\n\n"
        f"**Run:** commit `{report.commit}`, {report.date}, policy engine {report.policy_engine}\n\n"
        "**Reproduce:** `just redteam` (suite), `just redteam-promptfoo` or `just redteam-garak` "
        "(attack libraries). This issue closes itself when the attack holds in a later run.\n\n"
        f"<!-- redteam-fingerprint: {r.fingerprint} -->"
    )


def labels_of(r: Result) -> list[str]:
    return ["redteam", r.asi, f"severity:{r.severity}"]


def plan(report: Report, issues: Sequence[dict[str, Any]]) -> list[Action]:
    by_fp: dict[str, dict[str, Any]] = {}
    for known in issues:
        m = MARKER.search(known.get("body") or "")
        if m:
            by_fp[m.group(1)] = known
    actions = []
    for r in report.results:
        issue = by_fp.get(r.fingerprint)
        if r.outcome == "finding":
            if issue is None:
                actions.append(Action("open", r.fingerprint, title_of(r)))
            elif issue.get("state") == "closed":
                actions.append(Action("reopen", r.fingerprint, title_of(r), int(issue["number"])))
            else:
                actions.append(Action("comment", r.fingerprint, title_of(r), int(issue["number"])))
        elif r.outcome == "held" and issue is not None and issue.get("state") == "open":
            actions.append(Action("close", r.fingerprint, title_of(r), int(issue["number"])))
    return actions


def apply(report: Report, actions: Sequence[Action], gh: GitHub) -> None:
    results = {r.fingerprint: r for r in report.results}
    for a in actions:
        r = results[a.fingerprint]
        seen = f"Seen again in the red-team run of commit `{report.commit}` ({report.date}): {r.detail}"
        if a.kind == "open":
            gh.create(a.title, body_of(r, report), labels_of(r))
        elif a.kind == "reopen" and a.number is not None:
            gh.set_state(a.number, "open")
            gh.comment(a.number, f"Reopened. {seen}")
        elif a.kind == "comment" and a.number is not None:
            gh.comment(a.number, seen)
        elif a.kind == "close" and a.number is not None:
            gh.comment(
                a.number,
                f"The attack held in the red-team run of commit `{report.commit}` "
                f"({report.date}); closing.",
            )
            gh.set_state(a.number, "closed")


def open_blocking(gh: GitHub) -> list[dict[str, Any]]:
    issues = gh.issues("open")
    return [
        {"number": i["number"], "title": i["title"], "labels": [lb["name"] for lb in i["labels"]]}
        for i in issues
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="redteam_issues")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("reports", nargs="+", type=Path)
    s.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    reports = [Report.load(p) for p in args.reports if p.exists()]
    if not reports:
        print("redteam-issues: no report to sync", file=sys.stderr)
        return 0
    report = merge(reports)
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        print("redteam-issues: set GITHUB_REPOSITORY and GITHUB_TOKEN", file=sys.stderr)
        return 2
    gh = GitHub(repo, token)
    actions = plan(report, gh.issues())
    for a in actions:
        print(f"redteam-issues: {a.kind} {f'#{a.number} ' if a.number else ''}{a.title}")
    if not actions:
        print("redteam-issues: nothing to change")
    if not args.dry_run:
        apply(report, actions, gh)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
