# Runs no longer wait for telemetry flushes

**Step:** 34 · **Commit:** fix(agent-compiler): never block a run on a telemetry flush · **Date:** 2026-10-16

## What changed
- **Workers don't flush.** Team workers (`run_worker`) no longer flush spans; the team's run owns the trace.
- **Runs flush only `just run`'s own exporter.** `run()` flushes only the fallback provider that `just run` creates for itself (`tracing.owns`). It does so off the event loop (`asyncio.to_thread`) with a 5-second cap.
- **Service providers are never flushed by a run.** They already export in the background (batch span processor).

## Why
The Step 34 eval harness found it. Every worker delegation called `force_flush` on the OTLP exporter synchronously, inside the async run. With no collector listening, each flush spent about 6 seconds in retries, blocking the event loop. A support-handoff run took 18 s instead of a fraction of a second. In production, a slow or unreachable collector would have stalled every team run the same way.

## How to check it
- `uv run pytest libs/agent-compiler/tests/test_tracing_flush.py`: a team run with its workers calls no flush, and only the CLI's fallback provider counts as owned.
- The team eval tests went from 6 min 19 s to about 5 s.

## Risks and follow-ups
`just run` still flushes once at the end, capped at 5 s, so a short CLI run's spans reach the collector before the process exits.
