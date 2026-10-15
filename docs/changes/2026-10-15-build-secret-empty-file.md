# Fix the ops image build on newer Docker Buildx

**Step:** 01 · **Commit:** fix(infra): use an empty CA file as the default build secret · **Date:** 2026-10-15

## What changed
The optional "extra CA" build secret defaulted to `/dev/null`. Newer Docker Buildx (bake)
treats reading a host path outside the project as a privilege and refuses it, which broke
`just up` in CI. The default is now an empty file inside the repo,
`infra/compose/no-extra-ca.pem`.

## Why
The secret slot is there for corporate proxies that re-sign HTTPS traffic. On a normal Mac
there is nothing to pass, so the slot should be empty without needing special permission.

## How to check it
`docker compose -f infra/compose/compose.yaml build` no longer prints
`additional privileges requested`; CI's integration job passes `just up`.

## Risks and follow-ups
None; behind a proxy, set `EXTRA_CA_BUNDLE=/path/to/ca.pem` as before.
