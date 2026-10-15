# A duplicate call no longer fails when the first one finishes at the wrong moment

**Step:** 17 (fixes Step 6) · **Commit:** fix(agent-tools): return the first result when a duplicate call races its completion · **Date:** 2026-10-15

## What changed
When two workers make the same call (same idempotency key), the second waits for the first's
result. It read "no result yet", then checked "still running?". If the first stored its result
between those two reads, the second wrongly reported "still in progress" with no output. It now
reads the result once more before giving up, and reports a call that was abandoned (not merely
still running) with its own message.

## Why
CI run 26 failed `test_valkey_idempotency_across_executors` on a timing it had never hit before;
the Step 17 changes did not touch this code. A duplicate of a write must return the first
result, never a spurious failure that invites a retry.

## How to check it
`uv run pytest libs/agent-tools/tests/test_tools.py` — a new test forces exactly that ordering;
it fails on the old code and passes now. The Valkey integration test covers the real store.

## Risks and follow-ups
None known.
