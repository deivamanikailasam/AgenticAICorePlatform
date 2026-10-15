# A model can now write plans and review answers

**Step:** 14 · **Commit:** feat(agent-runtime): add the LLM planner and critic · **Date:** 2026-10-15

## What changed
- `planner.llm` (`LlmPlanner`) asks the platform model client for a plan as structured output:
  numbered steps (`E1`, `E2`, …), each with an optional tool, optional tool arguments and the
  steps it depends on. A plan naming a tool the agent was not given, or depending on a step that
  does not exist, is refused.
- `critic.llm` (`LlmCritic`) asks a model to score an answer from 0 to 1, say whether it passes
  and give feedback. Empty answers fail at once without a model call. The role it uses is a
  setting: `judge` for a separate evaluator, or the agent's own role for self-critique.
- `PlanStep` gains `arguments`, which may refer to earlier results (`#E1`, `#E1.words`).
- Both pass the existing `PlannerContract` and `CriticContract`.

## Why
Planning and reflection patterns (the rest of Step 14) need someone to write the plan and
someone to mark the work. Keeping them as slots means a team can swap in a rules-based planner
or a different judge without touching the patterns.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_reasoning.py` — 5 passed.

## Risks and follow-ups
A plan is only as good as the model that wrote it; the plan-execute pattern re-plans when a step
fails. Critic scores from a small local model can be noisy; Step 32's evals measure judges.
