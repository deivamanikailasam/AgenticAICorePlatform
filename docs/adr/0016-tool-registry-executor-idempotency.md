# ADR 0016: Postgres tool registry, one executor door, Valkey idempotency and S3 spill

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 06
- **Deciders:** repository owner

## Context
Step 6 defines how tools are described, found and invoked safely. Principle 7: every side effect
is classified, authorised and idempotent; irreversible actions require approval and are never
retried automatically.

## Decision
1. **Spec.** `ToolSpec` carries a model-safe `name`, a source `ref`, version, input/output JSON
   Schemas, `side_effect` (`read`/`write`/`irreversible`), `idempotent`, timeout, retry policy,
   data classification, owner and deprecation.
2. **Registry.** Default `tool_registry.postgres`: specs in `agent_tools.tool_specs` (Project 0
   Postgres, `agent` DB), keyed by `(namespace, name, version)`; implementations are bound per
   process and a missing binding is an explicit error. `tool_registry.in_memory` for tests.
3. **Executor.** `tool_executor.platform` is the only path to a tool. Order: lookup → schema
   validation (before execution) → idempotency key required for `write`/`irreversible` → replay
   on a repeated key (atomic claim, so concurrent duplicates run once) → timeout → retries only
   for idempotent, non-irreversible tools → output schema → spill to S3 above 64 KiB.
4. **Irreversible outcomes are sticky.** Even a failed irreversible call is stored under its key,
   so it is never re-attempted automatically; a human decides.
5. **Stores.** Idempotency in Project 0's Valkey (`agent:idem:<tenant>:<tool>:<key>`, TTL 24 h);
   artefacts in Project 0's SeaweedFS S3 (`s3://agent-artefacts/tool-outputs/<tenant>/<run>/<call>.json`)
   via boto3 1.43.x (path-style, SigV4).
6. **Migrations.** Plain SQL files per library, applied once by `just migrate`, checksummed; an
   edited, applied migration is an error.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| In-memory registry everywhere | Processes would disagree on deprecations |
| Idempotency in Postgres | Works, but Valkey's `SET NX EX` gives atomic claims with expiry for free |
| Retry irreversible tools on timeout | Could pay twice; the spec forbids it |
| Alembic for migrations | Heavier; plain SQL + checksums is enough until schema churn grows |
| Inline large outputs | Blows the context window and the checkpoint size |

## Consequences
Every caller must supply idempotency keys for writes (the runtime derives them deterministically
from run, node and call index from Step 11). Tool authors only implement `invoke`.

## Verification
`libs/agent-tools/tests/test_tools.py` (unit, contracts) and `test_tools_integration.py`
(Postgres, Valkey, S3 on the stand-in).
