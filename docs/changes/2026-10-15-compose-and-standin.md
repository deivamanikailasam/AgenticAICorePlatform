# Plug Project A into Project 0's network, and add a test stand-in

**Step:** 01 · **Commit:** feat(infra): join Project 0 network and add stand-in stack for CI · **Date:** 2026-10-15

## What changed
`infra/compose/compose.yaml` declares Project A's containers. It does not create its own
network: it joins Project 0's (`external: true`), so Project A reaches Project 0's gateway,
Postgres, Valkey, Keycloak, OPA and OTel Collector by name. The first container,
`agent-health`, runs the health check from inside that network; `just up` builds and runs it.

`tests/stack/compose.p0-standin.yaml` is a **stand-in** for Project 0, used only in CI and in
cloud sessions where the Mac is not reachable. It runs the same upstream images (pinned by
digest) with throwaway passwords and a fake model that returns canned text.

## Why
Project A is a new shop in an existing building: it plugs into the building's wiring rather
than installing its own. The stand-in is a test rig on the workbench, so the plugs can be tested
before the shop moves into the real building.

## How to check it
```
just standin-up                         # CI / cloud only
set -a; . tests/stack/standin.env; set +a
just health                             # six rows, all OK
just test-integration                   # 1 passed
```
On the Mac, skip the stand-in: start Project 0, then `just health` and `just up`.

## Risks and follow-ups
The service names (`litellm`, `postgres`, …), realm name and network name are assumptions until
compared with Project 0's compose file on the Mac (VERIFY-ON-MAC). The compose check now allows
locally built images only if every `FROM` line in their Dockerfile is pinned by digest.
