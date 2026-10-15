# A2A streaming lifecycle test

**Step:** 24 · **Commit:** test(a2a-gateway): streaming clients see the task lifecycle · **Date:** 2026-10-15

## What changed
A new test calls a published agent with a streaming A2A client (`SendStreamingMessage`). It
checks that the client receives:
- the task first;
- a "working" status;
- the answer as an artifact update;
- "completed" at the end.

## Why
Published Agent Cards advertise `streaming: true`. That claim needs a test, just as the
non-streaming path has one.

## How to check it
`uv run pytest services/a2a-gateway/tests -k streaming`

## Risks and follow-ups
The stream reports task states and the final artifact. It does not yet carry token-by-token
text from the run's AG-UI stream, which would need incremental reads of the run API's events.
This is a follow-up.
