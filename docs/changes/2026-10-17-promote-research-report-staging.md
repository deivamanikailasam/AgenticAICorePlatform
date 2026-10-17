# Promote research-report 0.2.0 to staging

## What changed
- `just promote research-report --to staging` snapshotted 0.2.0 into
  `deploy/specs/research-report/0.2.0/`: the app, its team and its five workers.
- Staging now serves it, and its release config is regenerated.

## Why
This is the first step of the critic and approval version (Step 40) towards production.

## How to check it
`just promotion-check --base HEAD~1`: 0 problems.

## Risks and follow-ups
Prod takes it as a 10% canary next.
