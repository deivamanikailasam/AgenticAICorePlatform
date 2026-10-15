# Temporal server for development and CI, with restart tests

**Step:** 19 · **Commit:** test(agent-runtime): prove Temporal runs survive restarts and long waits · **Date:** 2026-10-15

## What changed
- New compose service `temporal`, behind the `temporal` profile. It is the Temporal CLI dev
  server 1.9.1, pinned by digest and bound to 127.0.0.1 (gRPC on 7233, UI on 8233).
- Its SQLite history lives on the `temporal-data` volume, so it survives a restart.
- New recipe `just up-temporal`; `just down` also stops the Temporal profile.
- New integration tests in `libs/agent-runtime/tests/test_temporal_integration.py`:
  - the `durable_executor.temporal` contract;
  - a payment run that pauses for approval. The test stops the worker, restarts the server,
    then approves on a new worker. The run finishes, and the workflow history scheduled
    `record` and `pay` once each;
  - a 24-hour approval wait, and expiry after the 7-day timer. Both run in Temporal's
    time-skipping test server.
- CI starts Temporal and sets `AGENT_REQUIRE_TEMPORAL` and `AGENT_REQUIRE_TEMPORAL_TIMESKIP`, so
  these tests fail instead of skipping there.

## Why
These are the Step 19 Done-when checks: a run waits 24 hours (simulated) for an approval,
survives a worker and server restart, then finishes, and its history shows no duplicate side
effects.

## How to check it
`just up-temporal && uv run pytest -m integration libs/agent-runtime/tests/test_temporal_integration.py -rs`

## Risks and follow-ups
- The time-skipping server is downloaded from temporal.download on first use. This sandbox
  blocks that download, so those two tests skip locally and run in CI.
- The dev server is for development only. Production uses a Temporal cluster (or Temporal
  Cloud) with its own database (Step 37).
