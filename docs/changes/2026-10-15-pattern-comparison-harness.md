# A fair test bench for comparing agent patterns

**Step:** 14 · **Commit:** feat(agent-compiler): add the pattern comparison harness and reference tasks · **Date:** 2026-10-15

## What changed
- `evals/reference/planning-tasks/` holds 3 reference tasks (word count × 7, GST total,
  character difference) with the expected answer for each.
- `agent_compiler.compare` runs every pattern (`react`, `plan_execute`, `rewoo`, `reflection`,
  `evaluator_optimiser`) on the same tasks, with the same tools and model, and meters at the
  model-client and tool-executor boundary: model calls, tool calls, tokens in and out, and cost
  in AUD as the gateway reports it. Its table always states the dataset, its size, the model
  alias and when it ran.
- `just compare-patterns` runs it against Project 0's gateway.
- A test runs all five patterns on all 3 tasks on **both** runtime adapters with a scripted
  stand-in model, checking every task is solved and every call is metered.

## Why
"Planning saves money" is a claim; this measures it on the same tasks, so a team can see the
trade-off for its own model before choosing a pattern.

## How to check it
`uv run pytest libs/agent-compiler/tests/test_compare_patterns.py` — 3 passed. On the Mac:
`just compare-patterns`.

## Risks and follow-ups
No comparison numbers are published yet: the stand-in model's counts describe its script, not a
model. Real numbers come from `just compare-patterns` on the Mac (VERIFY-ON-MAC) and are quoted
with the dataset size (3) and model. Three tasks is a smoke test, not a benchmark.
