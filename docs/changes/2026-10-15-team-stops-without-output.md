# A team that ends without its answer stops the run instead of succeeding

**Step:** 22 · **Commit:** fix(agent-runtime): stop a team run that ends without its output · **Date:** 2026-10-15

## What changed
When a supervisor's `output` field (for example `report`) is not in the shared state at the end,
the node raises `StopRun`. This happens when the budget is used, every circuit is open,
`max_rounds` is reached, or the dispatcher finishes early. The run ends with status `stopped`
and keeps the rounds and per-agent cost as partial results.

## Why
While wiring the API, a misconfigured run had every worker fail, yet the team still reported
`succeeded` with no report. A run must not claim success without the answer it exists to
produce.

## How to check it
`uv run pytest libs/agent-compiler/tests/test_supervisor.py`: the budget-capped team now ends
`stopped`, with its rounds kept.

## Risks and follow-ups
None known. Teams without an `output` field still succeed with their whole state.
