# A labelled set and a scorecard for routers

**Step:** 15 · **Commit:** feat(evals): add the routing labelled set and accuracy harness · **Date:** 2026-10-15

## What changed
- `evals/labelled/routing/`: 48 held-out support requests labelled `billing`, `technical`,
  `sales` or `account` (12 each), 24 separate training examples for the embedding router, and
  the rules router's patterns.
- `agent_compiler.route_eval` routes every held-out case with each router and reports accuracy,
  precision and recall per route, a confusion table, mean latency and model cost. A router error
  counts as a wrong answer. The report states the dataset path and size, the model alias behind
  each router and the time of the run.
- `just eval-routing` runs the LLM, embedding and rules routers against Project 0's gateway.

## Why
The spec's "Done when" for Step 15: routing accuracy is measured on a labelled set. Measuring
all three routers on the same cases shows the trade-off between cost, speed and accuracy.

## How to check it
`uv run pytest libs/agent-compiler/tests/test_route_eval.py` checks the arithmetic on known
predictions, the set's shape (balanced, train and test disjoint) and that every router runs all
48 cases. On the Mac: `just eval-routing`.

## Risks and follow-ups
No accuracy figures are published yet: the real routers need Project 0's models
(VERIFY-ON-MAC). The set is small and synthetic, and the rules were written by the same author
as the cases, so the rules router's score on it is optimistic.
