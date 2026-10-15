# Inspect and flush long-term memory

**Step:** 20 · **Commit:** feat(agent-memory): inspect and flush memory · **Date:** 2026-10-15

## What changed
- **Two new memory store methods**, in the contract and every store (pgvector, Qdrant, Mem0,
  the dummies and the stubs):
  - `items(scope)` lists visible, unexpired items, newest first;
  - `flush_run(tenant, run_id)` removes everything one run wrote.
- **New run API endpoints:**
  - `GET /v1/memories?agent=A`: a user sees what agent A remembers for them (their own items
    and shared ones), with source, trust, run and expiry;
  - `DELETE /v1/memories/{agent}/{id}`: a user deletes one of their own memories. Shared items
    are refused.
  - `POST /v1/admin/memory/flush {"agent", "run_id"}`: a privacy or security officer removes
    everything one run wrote. This is audited as `memory.flushed`.

## Why
OWASP ASI06 lists "inspect-and-flush tooling" as a control. When a run is later found to have
been steered, its memories can be removed in one call. The Australian Privacy Principles also
give people access to the personal information held about them.

## How to check it
- `uv run pytest services/agent-api/tests/test_erasure.py`: users see only their own and shared
  memories, can delete only their own, and an officer's flush removes exactly the bad run's
  item.
- The store contract runs against pgvector, Qdrant and Mem0:
  `uv run pytest -m integration libs/agent-memory` (35 passed in this build).

## Risks and follow-ups
Listing scrolls each Qdrant collection up to the limit. Pagination comes with the admin UI.
