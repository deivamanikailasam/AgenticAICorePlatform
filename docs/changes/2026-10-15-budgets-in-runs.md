# Budgets and loop detection in every run; graceful stop with the reason

**Step:** 26 · **Commit:** feat(agent-runtime): enforce budgets and stop looping agents in runs · **Date:** 2026-10-15

## What changed
- `agent_runtime.budget.RunBudgets` opens a budget per run from the spec. Child runs (team
  workers) open under their parent. It closes the budget at the end.
- Two new wrappers enforce the budget:
  - `BudgetedModelClient` charges tokens and AUD per call.
  - `BudgetedToolExecutor` charges tool calls, in total and per tool, before each call. It also
    watches for repeated calls that make no progress (same call, same result).
- A trip raises `StopRun("budget: <limit>: <numbers>")` and a loop raises `StopRun("loop: …")`.
  The run ends `stopped` with its partial transcript, and the run record names the limit (for
  example `budget: max_cost_aud: AUD 1.0200 over the limit of 1.0`, or
  `loop: repeated call: calculator{"expression":"1+1"} 3 times in the last 3 steps`).
- Spec `budgets` gains three settings:
  - `max_calls_per_tool`;
  - `on_exceed: stop | summarise` (the spec's "hard stop → stop + summarise");
  - `loop` (enabled, repeat, window, no_progress, oscillation).

  `summarise` adds a short summary of the partial result from one capped model call
  (300 tokens), made outside the exhausted budget.
- Where it is wired:
  - the run API's assembly wraps every model and tool executor;
  - the run manager opens and finishes budgets (approvals keep a budget open);
  - the compiler passes budgets to child runs;
  - the agent loop keeps its transcript when a budget stops it;
  - `RUN_FINISHED` now carries the output of stopped runs too (partial, budget, reason).

## Why
Step 26's "Done when": a deliberately looping agent stops within its limits, and the run record
shows which limit tripped.

## How to check it
`uv run pytest services/agent-api/tests/test_budgets.py`: 6 tests, each an agent whose model
never answers.
- Identical calls stop at the third call, by loop detection.
- Varying calls costing AUD 0.03 each stop at AUD 1.02 against a limit of AUD 1.00. That is
  34 model calls; the call that crossed the limit is the last one.
- `max_tool_calls`, `max_calls_per_tool` and `max_tokens` are each named in the run record.
- `summarise` adds the summary.

## Risks and follow-ups
- A budget can overshoot by at most one model call, because cost is known only after the call.
  The gateway's key budget is the hard ceiling.
- After a process restart, a paused run's budget restarts from zero.
