# The knowledge assistant's first prod release

## What changed
`just promote knowledge-assistant --to prod` made staging's 0.1.0 prod's stable version. This
is an agent's first prod release, so there is no stable version to compare a canary with.

## Why
All three reference apps (Step 40) are now released in every environment.

## How to check it
`just promotion-check --base HEAD~1`: 0 problems. On main this needs the named approver, because
`deploy/releases/prod.yaml` and `deploy/specs/` are governance paths.

## Risks and follow-ups
Its next version goes through a 10% canary with online evaluation.
