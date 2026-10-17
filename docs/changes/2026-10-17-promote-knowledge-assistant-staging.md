# Promote the knowledge assistant to staging

## What changed
- `just promote knowledge-assistant --to staging` snapshotted version 0.1.0 into
  `deploy/specs/knowledge-assistant/0.1.0/`.
- Staging now serves it (`deploy/releases/staging.yaml`).
- The staging release config is regenerated.

## Why
This is the knowledge assistant's first step from dev to production, through the order Step 38
enforces.

## How to check it
`just promotion-check --base HEAD~1`: 0 problems.

## Risks and follow-ups
Its first prod release follows in its own commit.
