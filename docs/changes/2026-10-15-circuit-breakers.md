# Circuit breakers on failing tools and models

**Step:** 26 · **Commit:** feat(agent-runtime): circuit breakers on failing tools and models · **Date:** 2026-10-15

## What changed
- `agent_runtime.circuit.CircuitBreaker` keeps one breaker per key. After N consecutive
  failures it opens and refuses calls at once. After a cooldown it lets one trial call through:
  success closes it, failure re-opens it.
- `CircuitBreakingToolExecutor`:
  - counts timeouts, failures and rate limits, but not denials, bad arguments or approvals;
  - while a tool's circuit is open, returns `ToolErrorCode.UNAVAILABLE` (new) with the retry
    time.
- `CircuitBreakingModelClient`: per model role or alias, an open circuit ends the run with
  `StopRun("circuit open: model …")`. The run is `stopped` with its partial state.
- The run API's assembly wraps every run's tools (5 failures, 30 s cooldown) and models
  (3 failures, 30 s cooldown). The breakers are shared by every run in the process.

## Why
Step 26 asks for circuit breakers on failing tools and models. Without them, every run keeps
calling a broken tool or a down model, burning time and budget and adding load to the outage.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_circuit.py`. It covers:
- open, then half-open with the trial failing, then closed;
- denials are not counted;
- the model circuit stops the run, with a separate circuit per role.

## Risks and follow-ups
- The thresholds are fixed in the assembly for now. Per-tool settings can come with the tool
  registry if a tool needs different values.
