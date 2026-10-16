"""CI eval gates (Step 36): run the affected apps' golden tasks and decide whether the change may
merge (``evals/gates.yaml``).

    eval_gate [APP ...] [--base REF] [--redteam REPORT ...] [--provider stand-in|gateway]
              [--approve cost,signoff] [--github] [--comment]

* Apps: those named, or those a change touches (``--base``: the files changed since REF). A
  spec, an app's code or tasks touch that app; shared code (libs, the run API, platform.yaml with
  its models, policies, the gates file) touches every app; docs touch none.
* Each app runs through the run API (``agent_api.eval_runner``) and is judged against its
  baseline (``evals/baselines/<app>.mechanics.json`` for the stand-in, ``.quality.json`` for a
  real model) with the gates of its spec's risk tier.
* ``--redteam``: the red-team reports to count (required for medium and high tiers).
* ``--github``: approvals come from pull-request reviews: an APPROVED review on the head commit,
  by an approver for the app's tier (governance/approvers.yaml) who is not the author, containing
  ``/approve-cost`` or ``/eval-signoff``. ``--approve`` grants them locally (for trying it out).
* ``--comment``: post (or update) one comment on the pull request with the results.

Writes ``evals/results/gates/<app>.json`` and ``summary.md``. Exit 1 if any app is blocked or
waits for an approval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
import yaml
from agent_api.eval_runner import evaluate
from agent_evals.gates import GateResult, GatesConfig, RunSummary, markdown
from agent_evals.gates import evaluate as judge
from agent_evals.redteam import Report as RedTeamReport
from agent_evals.redteam import merge

from scripts.eval_app import apps as all_apps
from scripts.eval_app import harness, provider

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "results" / "gates"
MARKER = "<!-- eval-gates -->"
SHARED = (
    "libs/",
    "services/agent-api/",
    "services/agent-worker/",
    "services/mcp-gateway/",
    "platform.yaml",
    "policies/",
    "evals/gates.yaml",
    "evals/rubrics/",
    "pyproject.toml",
    "uv.lock",
)


def affected(paths: Iterable[str], known: Iterable[str]) -> list[str]:
    """Which apps a change touches. Shared code touches all of them."""
    names = sorted(known)
    hit: set[str] = set()
    for path in paths:
        if path.startswith(SHARED):
            return names
        for app in names:
            if path.startswith(
                (f"agents/{app}/", f"apps/{app}/", f"evals/apps/{app}/", f"evals/baselines/{app}.")
            ):
                hit.add(app)
    return sorted(hit)


def changed_since(base: str) -> list[str]:
    out = subprocess.run(  # noqa: S603 - fixed git command
        ["git", "diff", "--name-only", f"{base}...HEAD"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


# ---------------------------------------------------------------- GitHub (approvals, comment)


def _gh() -> tuple[httpx.Client, str, dict[str, Any]] | None:
    token, repo, event = (
        os.environ.get("GITHUB_TOKEN"),
        os.environ.get("GITHUB_REPOSITORY"),
        os.environ.get("GITHUB_EVENT_PATH"),
    )
    if not (token and repo and event and Path(event).exists()):
        return None
    pr = json.loads(Path(event).read_text()).get("pull_request")
    if not pr:
        return None
    client = httpx.Client(
        base_url="https://api.github.com",
        timeout=30,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    return client, repo, pr


def approvals_from_reviews(
    reviews: Iterable[dict[str, Any]], head: str, author: str, approvers: Iterable[str]
) -> set[str]:
    """``cost`` / ``signoff`` granted by an APPROVED review on the head commit, from a listed
    approver who isn't the author. Teams (``@org/team``) are not expanded here."""
    allowed = {a.lstrip("@").lower() for a in approvers if "/" not in a}
    granted: set[str] = set()
    for r in reviews:
        user = str((r.get("user") or {}).get("login", "")).lower()
        if r.get("state") != "APPROVED" or r.get("commit_id") != head:
            continue
        if user == author.lower() or user not in allowed:
            continue
        body = str(r.get("body") or "")
        if "/approve-cost" in body:
            granted.add("cost")
        if "/eval-signoff" in body:
            granted.add("signoff")
    return granted


def github_approvals(tier: str) -> set[str]:
    ctx = _gh()
    if ctx is None:
        return set()
    client, repo, pr = ctx
    cfg = yaml.safe_load((ROOT / "governance" / "approvers.yaml").read_text()) or {}
    approvers = (cfg.get("tiers") or {}).get(tier) or cfg.get("default") or []
    resp = client.get(f"/repos/{repo}/pulls/{pr['number']}/reviews", params={"per_page": 100})
    resp.raise_for_status()
    return approvals_from_reviews(resp.json(), pr["head"]["sha"], pr["user"]["login"], approvers)


def comment(body: str) -> None:
    ctx = _gh()
    if ctx is None:
        print("eval-gate: not a pull request; no comment posted")
        return
    client, repo, pr = ctx
    text = f"{MARKER}\n{body}"
    found = client.get(f"/repos/{repo}/issues/{pr['number']}/comments", params={"per_page": 100})
    found.raise_for_status()
    mine = next((c for c in found.json() if MARKER in (c.get("body") or "")), None)
    if mine:
        client.patch(
            f"/repos/{repo}/issues/comments/{mine['id']}", json={"body": text}
        ).raise_for_status()
    else:
        client.post(
            f"/repos/{repo}/issues/{pr['number']}/comments", json={"body": text}
        ).raise_for_status()


# ---------------------------------------------------------------- the gate


def gate_app(
    app: str,
    kind: str,
    gates: GatesConfig,
    redteam: RedTeamReport | None,
    approvals: set[str],
    github: bool,
) -> GateResult:
    h: Any = harness(app)
    spec = h.spec()
    tier = spec.risk_tier.value
    gate = gates.for_app(tier, spec.evals.gates if spec.evals else "default")
    report = asyncio.run(
        evaluate(h, ROOT / "evals" / "apps" / app / "tasks.jsonl", provider(kind, h))
    )
    current = RunSummary.from_report(report.to_json())
    path = ROOT / "evals" / "baselines" / f"{app}.{current.kind}.json"
    baseline = RunSummary.from_baseline(json.loads(path.read_text())) if path.exists() else None
    granted = approvals | (github_approvals(tier) if github else set())
    return judge(app, tier, gate, current, baseline, redteam, granted)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="eval_gate")
    parser.add_argument("apps", nargs="*")
    parser.add_argument("--base", help="judge the apps changed since this git ref")
    parser.add_argument("--redteam", nargs="*", type=Path, default=[])
    parser.add_argument("--provider", choices=["stand-in", "gateway"], default="stand-in")
    parser.add_argument("--approve", default="", help="comma-separated: cost, signoff")
    parser.add_argument("--github", action="store_true")
    parser.add_argument("--comment", action="store_true")
    args = parser.parse_args(argv)

    known = all_apps()
    names = args.apps or (affected(changed_since(args.base), known) if args.base else known)
    OUT.mkdir(parents=True, exist_ok=True)
    notes = [
        "Stand-in run: checks regressions, red-team findings and token/cost growth against the "
        "mechanics baseline; quality, AUD and latency ceilings apply to real-model runs."
        if args.provider == "stand-in"
        else "Real-model run."
    ]
    if not names:
        body = (
            "## Eval gates\n\nNo agent, prompt, tool, model or pattern change: nothing to gate.\n"
        )
        (OUT / "summary.md").write_text(body)
        print(body)
        if args.comment:
            comment(body)
        return 0
    reports = [RedTeamReport.load(p) for p in args.redteam if p.exists()]
    redteam = merge(reports) if reports else None
    gates = GatesConfig.load(ROOT / "evals" / "gates.yaml")
    approvals = {a.strip() for a in args.approve.split(",") if a.strip()}
    kind = "gateway" if args.provider == "gateway" else "stand-in"
    results = [gate_app(app, kind, gates, redteam, approvals, args.github) for app in names]
    for r in results:
        (OUT / f"{r.app}.json").write_text(json.dumps(r.to_json(), indent=1) + "\n")
    body = markdown(results, notes)
    (OUT / "summary.md").write_text(body)
    print(body)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(body)
    if args.comment:
        comment(body)
    return 0 if all(r.verdict == "pass" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
