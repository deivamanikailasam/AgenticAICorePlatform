# just audit-verify

**Step:** 31 · **Commit:** feat(scripts): just audit-verify · **Date:** 2026-10-15

## What changed
`just audit-verify` recomputes the audit trail's hash chain and names the first broken record:
- by default, the shared Postgres trail (`AGENT_AUDIT_DSN`, else `AGENT_DB_DSN`)
- with `--file <jsonl>` (repeatable), a local trail

Exit status: 0 when the chain is intact, 1 when it is broken, 2 when there is nothing to check.

## Why
"The audit trail can't be tampered with undetected" only helps if someone checks. This gives operators and CI one command to run.

## How to check it
`uv run pytest tests/scripts/test_audit_verify.py`:
- an intact file passes
- an edited record is named, with "hash does not match the event"
- a removed record is named, with "missing"
- with the stand-in, the shared trail verifies


## Risks and follow-ups
The check reads the whole chain. For a very long trail, a checkpointed verify (from the last verified sequence and hash) is a follow-up.
