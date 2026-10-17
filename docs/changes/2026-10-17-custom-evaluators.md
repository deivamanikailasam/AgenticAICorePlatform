# Eval tasks can use a team's own evaluators

## What changed
- `expected.custom` in a golden task names a team's own checks: `{name: config}`.
- An evaluator is a callable `(config, task, observation) -> Score`, registered under the
  `agent_platform.evaluators` entry-point group (`agent_evals.checks.EVALUATOR_GROUP`).
- `score()` runs every named evaluator after the platform's own checks. It fails closed: a name
  with no evaluator installed, an evaluator that raises, or one that doesn't return a `Score`
  fails the task, with the reason in the score's detail.

## Why
Step 39 asks for an SDK in which teams write their own evaluators. The platform's checks cover
status, answer, trajectory, policy, team, limits and conversation; a domain rule such as
"the GST is right" needed a change to the platform.

## How to check it
`uv run pytest libs/agent-evals/tests/test_core.py -k custom`.

## Risks and follow-ups
Evaluators are deterministic code the team owns and reviews. Judged checks stay with rubrics
and the calibrated judge (Step 34).
