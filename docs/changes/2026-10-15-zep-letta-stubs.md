# Zep and Letta memory stores as documented stubs

**Step:** 20 · **Commit:** feat(agent-memory): Zep and Letta as documented memory store stubs · **Date:** 2026-10-15

## What changed
`memory_store.zep` and `memory_store.letta` are registered as stubs. Every call refuses with a
pointer to ADR 0031, and a test pins that behaviour.

## Why
The spec lists both as alternatives. I checked each project's own README on 2026-10-15:
- **Zep:** the README says "Zep Community Edition is no longer supported". The supported product,
  Zep Cloud, needs an account and API key, and sends memory to a third party.
- **Letta:** the README says the repository's V1 API server, which held archival memory, is
  "retired", and points active projects to Letta Code. The current `letta/letta` image starts
  the Letta Code command-line app, not a memory server.

Building against an unsupported or retired server, or sending memory to a cloud service without
the owner's decision, would weaken what the platform promises. So both stay stubs until the
owner decides.

## How to check it
`uv run pytest libs/agent-memory/tests/test_stubs.py`

## Risks and follow-ups
To complete either, the owner supplies a cloud account and accepts the data-residency trade-off.
This is listed as an open issue in PROGRESS.
