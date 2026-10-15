# Audit events carry the trace id and the versions in force

**Step:** 31 · **Commit:** feat(agent-contracts): audit events carry trace id and versions · **Date:** 2026-10-15

## What changed
- `AuditEvent` has two new optional fields:
  - `trace_id`: the OpenTelemetry trace the event belongs to, the same id Tempo and Langfuse show.
  - `versions`: what was running: spec version and hash, plan hash, prompt refs, model per role, tool versions.
- `AuditEvent.chained()` returns exactly what the hash chain covers. Empty new fields are left out.
- The in-memory and JSONL sinks hash `chained()`.

## Why
Step 31 asks that every audit event answer "which trace?" and "which versions?" without a second lookup. Dropping the fields when they're empty means the hashed body of an event written before this change is byte-for-byte the same, so existing chains still verify.

## How to check it
`uv run pytest libs/agent-contracts libs/agent-audit -k "chain"`. The test `test_chains_written_before_step_31_still_verify` builds an old-style event and checks its hashed body hasn't changed.

## Risks and follow-ups
None known. A sink that hashes `model_dump()` directly instead of `chained()` would compute different hashes; all three sinks in the repo use `chained()`.
