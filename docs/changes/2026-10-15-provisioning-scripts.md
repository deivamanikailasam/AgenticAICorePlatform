# Create Project A's own space inside Project 0, safely and repeatably

**Step:** 01 · **Commit:** feat(provision): add idempotent agent DB, Keycloak client and LiteLLM keys · **Date:** 2026-10-15

## What changed
`just provision` creates, inside Project 0's shared services:

- a Postgres database `agent`, owned by a new `agent_app` role, with the `vector` and
  `pgcrypto` extensions and four schemas (runtime, memory, registry, tools);
- a Keycloak client `agent-platform` (confidential, service account on, browser login off);
- one LiteLLM virtual key per agent role (planner, executor, router, judge, summariser), each
  allowed only its models and capped by a budget from `infra/provision/litellm-keys.yaml`.

Every script is *idempotent*: run it twice and the second run only reports "exists". New key
values are written once to a private (0600) file for moving into OpenBao, never printed.

## Why
Think of a new tenant in an office building: it gets its own room, its own staff badge and its
own cost-centre code on the shared phone bill. Doing it with a script, not by hand, means the
same room is set up the same way on every laptop and every environment.

## How to check it
Run `just provision` twice. The second run prints only `... exists` lines.
`just test-integration` includes a test that asserts exactly that.

## Risks and follow-ups
Budget figures are starting guard limits, not measurements; Step 26 tunes them. On the Mac the
admin credentials come from `secrets/dev.enc.env` via `just with-secrets just provision`
(VERIFY-ON-MAC). Moving key values into OpenBao is automated in Step 29.
