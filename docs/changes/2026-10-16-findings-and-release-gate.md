# Red-team findings tracked as issues; high-severity findings block a release

**Step:** 35 (in progress) · **Commit:** feat(redteam): findings as GitHub issues and a release gate · **Date:** 2026-10-16

## What changed
- **`scripts/redteam_issues.py` and `just redteam-issues`.** Each finding maps to one GitHub issue, matched by the finding's fingerprint, which is kept as a marker in the issue body.
  - A new finding opens an issue labelled `redteam`, its ASI category and `severity:<level>`. The issue says what happened, on which commit, and how to reproduce it.
  - A repeat finding comments on its open issue, or reopens it if it was closed.
  - An attack that holds in a later run closes its issue: fixed and re-verified. An attack that didn't run leaves its issue alone.
- **Release gate.** `just release-gate <reports...> --github` refuses a release when any of these is true:
  - a merged report has a high or critical finding;
  - an OWASP agentic category has no executed test;
  - a report is missing;
  - a high or critical red-team issue is still open.
- **Workflows.**
  - `ci.yml` (integration and sandbox jobs) and `redteam.yml` sync findings to issues on `main`; `redteam.yml` also syncs on scheduled and manual runs.
  - `release.yml` is new. On a `v*` tag or a manual run, it runs the whole suite (unit, integration and gVisor tiers) and both attack libraries on the commit, syncs issues, then applies the gate. Publishing jobs added in later steps must `need` it.
  - Feature branches still fail CI on a blocking finding, but don't open issues.

## Why
Spec Step 35 says findings are tracked as issues and high-severity findings block a release. Matching on the fingerprint means one weakness is one issue across runs. Closing only after a run where the attack held means "fixed" is something the suite has shown, not something someone claimed.

## How to check it
- `uv run pytest tests/scripts/test_redteam_issues.py`: 3 tests against a fake GitHub API. They cover: open once; comment on a repeat; leave alone when not run; close when held; reopen on a regression; open issues block the gate.
- `just release-gate evals/results/redteam/report.json evals/results/redteam/promptfoo.json evals/results/redteam/garak.json` on this container's local reports: 76 attacks executed, 72 held, 4 blocking findings, so the release is BLOCKED. Those 4 are the live tests that need Keycloak and Alertmanager; they fail closed locally. In CI on the full stand-in, all 57 suite attacks held on 08ac601.

## Risks and follow-ups
- Issues are created with the workflow's `GITHUB_TOKEN`, only on `main` and on scheduled, manual and release runs, so they need the repository's issues feature turned on.
- Making `release.yml`'s gate a required step of every publish is part of the later production steps.
