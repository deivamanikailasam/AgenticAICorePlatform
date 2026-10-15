# Run every check automatically on every push

**Step:** 01 · **Commit:** ci: add GitHub Actions workflow with unit and stand-in integration jobs · **Date:** 2026-10-15

## What changed
A GitHub Actions workflow runs on every push and pull request:

1. **check**: the same `just check` a contributor runs locally, plus a check that *every*
   pushed commit (not only the last) carries a `docs/changes/` entry.
2. **integration**: starts the Project 0 stand-in, then runs `just health`, `just provision`,
   `just up` and the integration tests.

Every third-party action is pinned to a full commit SHA, with its version in a comment.

## Why
CI is the inspector who checks every delivery at the gate, the same way every time, even on a
Friday afternoon. Pinning actions by SHA stops a hijacked action tag from running new code in
our pipeline (a supply-chain defence; see OWASP ASI04).

## How to check it
Open the repository's *Actions* tab: both jobs are green for the latest push.

## Risks and follow-ups
Integration in CI uses the stand-in, not Project 0 itself; the Mac run stays the final check.
Image scanning, SBOMs and signing join the pipeline in Step 38.
