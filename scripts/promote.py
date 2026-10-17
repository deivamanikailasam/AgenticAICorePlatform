"""Promote an agent version, as a reviewable change (Step 38).

promote AGENT --to staging              # snapshot agents/AGENT at its version; staging serves it
promote AGENT --to prod [--shadow]      # staging's version becomes prod's 10% canary (or shadow);
                                        # an agent's first prod release goes in as stable
promote AGENT --to prod --full --api URL
                                        # the canary becomes prod's stable version, only if its
                                        # online evaluation passed (GET /v1/admin/releases,
                                        # token in AGENT_API_TOKEN)
promote AGENT --abort                   # drop prod's candidate (after a rollback, or by choice)

Each command edits deploy/specs and deploy/releases, regenerates the deployment config, checks
the promotion rules against the last commit, and with --commit puts it on a branch
`promote/<agent>-<version>-<env>` for a pull request. Merging the pull request (with its
approvals and checks) is the promotion: Argo CD then syncs the environment.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_governance.release_repo import PromotionError, ReleaseRepo

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="promote", description=__doc__.split("\n\n")[0])
    p.add_argument("agent")
    p.add_argument("--to", choices=["staging", "prod"])
    p.add_argument("--shadow", action="store_true", help="prod candidate in shadow mode")
    p.add_argument("--full", action="store_true", help="the prod canary becomes stable")
    p.add_argument("--abort", action="store_true", help="remove prod's candidate")
    p.add_argument("--api", help="prod run API, for the online-eval verdict (with --full)")
    p.add_argument("--verdict-file", help="a saved GET /v1/admin/releases response")
    p.add_argument("--commit", action="store_true", help="commit on a promote/ branch")
    args = p.parse_args(argv)
    if not (args.to or args.abort):
        p.error("give --to staging|prod or --abort")
    repo = ReleaseRepo(ROOT)
    try:
        verdict = (
            repo.online_verdict(args.agent, args.api, args.verdict_file) if args.full else None
        )
        step = repo.promote(
            args.agent,
            args.to,
            shadow=args.shadow,
            full=args.full,
            abort=args.abort,
            verdict=verdict,
        )
    except PromotionError as exc:
        raise SystemExit(f"promote: {exc}") from None
    repo.write_config()
    found = repo.check("HEAD", None)
    for line in found:
        print(f"promote: {line}", file=sys.stderr)
    if found:
        return 1
    print(f"promote: {step.message(args.agent)}")
    if args.commit:
        branch = repo.commit(args.agent, step)
        print(f"promote: on branch {branch}; push it and open a pull request")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
