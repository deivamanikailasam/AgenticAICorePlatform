# Run API: memory per agent and the right to erasure

**Step:** 20 · **Commit:** feat(agent-api): agents' long-term memory and right to erasure · **Date:** 2026-10-15

## What changed
- **Memory per agent.** The assembly builds each agent's memory from its spec once and caches
  it. Embeddings go through the gateway's `embedder` role. Content is encrypted with the
  checkpoint key when the durable store is on.
  - Agents that list `local://platform/remember` get the remember tool.
  - A spec that lists it without `memory.long_term` is refused when the run is created.
- **`POST /v1/admin/erasure {"user": ..., "tenant"?: ...}`.** It removes everything about one
  person in one tenant:
  - their runs, stopped first, with their event logs (memory store or Postgres);
  - their checkpoint threads;
  - every agent's long-term memory about them, including tenant-wide items learned from them.

  Only a caller with the `privacy-officer` realm role in that tenant may ask. The erasure
  itself is audited as `subject.erased` with who asked and the counts. This endpoint is the hook
  Project 0's data lifecycle calls.
- `RunStore.erase(tenant, user)` is implemented for the memory and Postgres run stores.
- **CI and compose.** The services get the agent database DSN (`AGENT_DB_DSN_INTERNAL`),
  because `invoice-triage` declares pgvector memory.

## Why
Step 20 Done-when: "erasure removes a user's memories everywhere". Under the Australian Privacy
Principles, an organisation must be able to correct or delete personal information it holds.

## How to check it
`uv run pytest services/agent-api/tests/test_erasure.py`
- A non-officer and another tenant's officer get 403.
- The officer's request reports 1 run, 1 thread and 3 memories erased: a preference, an
  episode and a tenant-wide fact about the user.
- Afterwards the user's run, checkpoints and memories are gone, and the other user's are intact.

## Risks and follow-ups
- The hash-chained audit log keeps the user id: it is append-only by design. Its
  pseudonymisation is part of the data lifecycle in Step 37.
- Registering this hook with Project 0's lifecycle job is VERIFY-ON-MAC.
- The `privacy-officer` role must be created in Project 0's Keycloak realm.
