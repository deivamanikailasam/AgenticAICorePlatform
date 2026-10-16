# Eval checks for teams and word limits; answers read from a team's answer field

**Step:** 34 (in progress) · **Commit:** feat(agent-evals): team trajectory checks and word limits · **Date:** 2026-10-16

## What changed
- **`expected.team`.** Checks which workers a team delegated to, taken from the audit trail's policy-allowed `agent.delegate` events: `workers_required`, `workers_forbidden`, `first_worker`, `counts` (exact delegations per worker) and `max_delegations`. The runner now records delegations.
- **`answer.max_words`.**
- **Text checks read the answer.** They read the value at `path`, else a team result's `answer` field, else the whole output. Team results also carry rounds and costs, which were being counted as the answer.

## Why
Trajectory quality for multi-agent apps means delegating to the right workers, in the right order, as often as needed. A blurb's word limit applies to the blurb, not to the record of how it was written.

## How to check it
`uv run pytest libs/agent-evals`: 17 tests, including a misrouted team and an over-long answer.

## Risks and follow-ups
None.
