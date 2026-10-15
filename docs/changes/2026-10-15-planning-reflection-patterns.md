# Four new agent patterns: plan-and-execute, ReWOO, reflection, evaluator-optimiser

**Step:** 14 · **Commit:** feat(agent-runtime): add planning and reflection strategies · **Date:** 2026-10-15

## What changed
- `pattern: plan_execute` — plan, carry out each step with a small ReAct run limited to that
  step's tool, re-plan the remaining steps when one fails (at most `max_replans`, default 2),
  then write the answer from the step results.
- `pattern: rewoo` — plan once with tool arguments, run the tools in order without asking the
  model again (later arguments filled from earlier results), then one call writes the answer.
- `pattern: reflection` — draft, self-critique with the same model role, revise; at most
  `max_rounds` (default 2).
- `pattern: evaluator_optimiser` — draft, a separate evaluator (role `judge`) scores, improve;
  returns the best-scoring candidate when rounds run out.
- Each is a pattern builder and works as a whole-spec pattern or as an `agent` node in a
  hybrid graph (`pattern: rewoo` on the node). One dispatcher routes agent nodes by their loop.
- `max_iterations` caps **every** model call the pattern makes (planner, critic, sub-steps);
  reaching it stops the run. Each run works on its own copy, so agent nodes can run in parallel.
- Tools that need approval are refused by these patterns; such agents use `pattern: react`.
- Planner and critic come from `components: { planner: ..., critic: ... }` (default `llm`).

## Why
ReAct asks the model after every tool call. For tasks whose steps can be seen in advance,
planning first can save model calls; for tasks where quality matters more than speed, a review
round can catch mistakes. Each has a cost, so each is a choice in the spec, not a default.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_strategies.py libs/agent-compiler/tests` — includes
ReWOO making exactly two model calls, a re-plan after a failed step, self-critique using the
agent's own role, the evaluator keeping the best candidate, and the cap covering every call.

## Risks and follow-ups
ReWOO cannot react to a surprising tool result: if a step returns something unexpected the
solver sees it but no new tool runs. Reflection with a weak model can talk itself out of a right
answer; evaluator-optimiser keeps the best-scoring candidate to limit that.
