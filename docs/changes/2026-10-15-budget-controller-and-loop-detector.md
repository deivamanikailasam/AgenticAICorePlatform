# Platform budget controller and loop detector

**Step:** 26 · **Commit:** feat(agent-runtime): hierarchical budget controller and loop detector · **Date:** 2026-10-15

## What changed
- `budget_controller.platform` (`agent_runtime.budget.PlatformBudgetController`) enforces
  per-run limits on:
  - steps, tokens, AUD cost and wall-clock time. Wall-clock time is measured from `open`, so a
    silent run still times out;
  - tool calls, in total and per tool (new `BudgetLimits.max_calls_per_tool` and
    `BudgetCharge.tool`);
  - delegation depth.

  Child budgets roll up to every ancestor, and once an ancestor trips, no child continues. The
  status names the limit and the numbers (new `BudgetStatus.detail` and `depth`).
- `loop_detector.platform` (`PlatformLoopDetector`) flags three patterns:
  - the same call repeated `repeat` times in `window` steps without progress;
  - `no_progress` consecutive steps that changed nothing;
  - two steps alternating (A B A B …) `oscillation` times.
- Both pass the shared contracts. New tests cover cost detail, per-tool limits, wall clock
  without reported time, depth, the parent trip stopping children, no-progress and
  oscillation.

## Why
Step 26 needs real implementations behind the contracts. Until now only the test dummies
existed. The next commit wires them into runs.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_budget.py libs/agent-contracts`

## Risks and follow-ups
- Budgets are held in process memory. A run whose work spans the API and the Temporal worker
  is metered in each process separately, and the gateway's own key budget (LiteLLM) is the
  cross-process backstop.
