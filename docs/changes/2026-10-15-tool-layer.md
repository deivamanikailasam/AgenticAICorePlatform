# Build the tool cage: registry, executor, idempotency, large-output spill and tool cards

**Step:** 06 · **Commit:** feat(tools): add tool registries, the platform executor, built-in tools and tool cards · **Date:** 2026-10-15

## What changed
A new library, `agent-tools`:

- **Registries.** `tool_registry.postgres` stores every tool spec (name, version, schemas, side
  effect, owner, deprecation) in the `agent` database so all processes agree on what exists;
  `tool_registry.in_memory` is for tests. Latest non-deprecated version wins; deprecated versions
  can still be fetched by exact version.
- **Executor** (`tool_executor.platform`), the one door every tool call goes through:
  arguments are checked against the schema *before* the tool runs; `write` and `irreversible`
  tools must carry an idempotency key; a repeated key returns the first result (even when two
  calls race); idempotent reads retry per policy; irreversible calls are **never** retried and
  their outcome is kept; output schemas are enforced; outputs over 64 KiB spill to S3 and the
  model gets a preview plus the artefact URI.
- **Stores.** Idempotency in memory or Project 0's Valkey; artefacts in memory or SeaweedFS S3.
- **Built-in tools.** `calculator` (safe arithmetic, no code), `clock`, `word_count`.
- **Migrations.** `just migrate` applies `libs/*/migrations/*.sql` once each and refuses a changed,
  already-applied file. `just provision` now also creates the artefact bucket.

## Why
Tools are how agents change the world, so they live in a locked cage with a sign-out sheet:
every tool is labelled (side effect, owner, data class), every request is checked before the
door opens, and repeating a request can never send a second payment.

## How to check it
`uv run pytest libs/agent-tools` (unit). With the stand-in: `just provision && just migrate &&
uv run pytest -m integration libs/agent-tools` — the Postgres registry passes the shared
registry contract; two executors racing on one key run the tool once (Valkey); a 64 KiB+ output
lands in S3.

## Risks and follow-ups
Idempotency results expire after 24 hours by default. Policy (OPA) wraps the executor in
Step 10; until then the executor never decides permissions.

**Tool cards.** `just cards` generates a card for every registered tool: machine-readable
`cards/tools/<name>.yaml` and a readable `docs/cards/tools/<name>.md` (purpose, owner, side
effect and its default rule, idempotency, timeout, retry, data class, schemas, limitations).
`just check` fails if a card is stale or orphaned.
