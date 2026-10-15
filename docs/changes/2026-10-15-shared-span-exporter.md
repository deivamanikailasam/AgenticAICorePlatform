# Share one span recorder across tests

**Step:** 08 · **Commit:** test: share one in-memory span exporter across test modules · **Date:** 2026-10-15

## What changed
A root `conftest.py` provides a `span_exporter` fixture. OpenTelemetry lets a process set its
global tracer provider only once, so two test modules that each installed their own recorder
silently lost spans when run together. Now every test uses the same recorder, cleared before
each test.

## Why
Running the whole integration suite in one go (as CI does) showed the MCP trace test passing
alone but failing with others — a test-isolation bug, not a product bug.

## How to check it
`uv run pytest -m integration` with the stand-in running: all integration tests pass together.

## Risks and follow-ups
None.
