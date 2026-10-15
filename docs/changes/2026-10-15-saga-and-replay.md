# Undo completed steps when a run fails, and replay runs exactly

**Step:** 12 · **Commit:** feat(agent-runtime): add saga compensation and journal replay · **Date:** 2026-10-15

## What changed
- **Saga:** when a run fails, every step that finished and names a `compensation` has its undo
  step run, newest first (refund the charge, then release the stock). A step that itself failed
  is not undone. The undo results are kept in the run state, reported as events, and the error
  says what was compensated.
- **Journal:** every node result is recorded per run, in order (loops record each pass), and
  can be saved as JSON.
- **Replay:** a runner built with `replay=journal` returns the recorded results instead of
  running anything, including approvals: no model, tool or function is called again, and the run
  takes exactly the same path. If the plan asks for more than was recorded, the replay fails with
  "replay diverged".
- Both adapters accept an injected `NodeRunner`, so the platform can record or replay a run on
  either engine.

## Why
A saga is the accounting rule "every entry has a reversing entry": a payment system cannot roll
back a database transaction across three external services, but it can issue refunds in order.
Replay is the flight recorder: investigate exactly what happened, as many times as needed,
without touching the real systems again.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_saga_and_replay.py` — compensation order on both
adapters, replay with nothing re-executed, journal JSON round trip.

## Risks and follow-ups
Compensations are best effort: if an undo step fails, the run still ends failed and the failed
undo is visible in its state; alerting on that comes with observability (Step 30). Journals live
in memory until checkpoints are durable (Step 18).
