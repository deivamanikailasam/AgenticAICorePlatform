# Long-term memory in pgvector, behind a write policy

**Step:** 20 · **Commit:** feat(agent-memory): pgvector memory store and the memory write policy · **Date:** 2026-10-15

## What changed
New package `libs/agent-memory`.

- **`memory_store.pgvector` (default).** Items live in `agent_memory.items` in the `agent`
  database (migration `0004_memory.sql`).
  - Search is cosine similarity, inside the caller's scope only. It skips expired items and
    older versions of procedural memory.
  - Content is encrypted with AES-256-GCM when a key is set, bound to tenant and item.
  - Erasure removes a user's items and every item about them.
  - `purge_expired` deletes expired items.
- **`memory_policy.validating` (default).** It refuses:
  - items with no provenance source;
  - untrusted content that has not been validated;
  - instructions (procedural memory) unless they come from the platform or a user who
    confirmed them;
  - content the injection guardrail flags;
  - items below the minimum confidence.

  It then redacts personal data and sets the item's lifetime: 365 days for facts, 90 for
  episodes, and no limit for instructions until they are replaced. Facts and episodes are
  capped at 730 days. These are configurable defaults.
- **`memory_policy.human_curated`.** Stores only items a person curated. Every other write is
  queued for review.
- The baseline PII guardrail now redacts phone numbers as well as e-mail addresses.

## Why
Step 20: memory that helps the agent without becoming a persistent prompt-injection channel or
a store of unredacted personal data.

## How to check it
- `uv run pytest libs/agent-memory`
- `just migrate && uv run pytest -m integration libs/agent-memory`: the store contract, plus
  encryption at rest, against Postgres with pgvector.

## Risks and follow-ups
- Search is exact (no ANN index), because the column accepts any embedding dimension. Add an
  HNSW index once the embedding model is fixed.
- Presidio and the injection classifier replace the baseline guardrails in Step 27.
