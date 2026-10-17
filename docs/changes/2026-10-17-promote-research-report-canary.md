# research-report 0.2.0 as prod's 10% canary

## What changed
`just promote research-report --to prod` made staging's 0.2.0 a 10% canary in prod, beside the
stable 0.1.0.

## Why
A new version serves a tenth of new conversations first. The online evaluation compares it with
the stable version and rolls it back on its own if it does worse (Step 38).

## How to check it
`just promotion-check --base HEAD~1`: 0 problems. On main this needs the named approver.

## Risks and follow-ups
- `just promote research-report --to prod --full --api <prod run API>` makes it stable, but only
  once the live online evaluation says `pass`.
- No production traffic exists in this build, so the canary stays a canary here.
