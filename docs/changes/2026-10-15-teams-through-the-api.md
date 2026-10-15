# Teams run through the run API

**Step:** 22 · **Commit:** feat(agent-api): run supervisor teams through the run API · **Date:** 2026-10-15

## What changed
- The assembly gives supervisor nodes a worker factory. Each worker is assembled like any
  agent: its own model roles, its own registry with only its spec's tools, and its own grants.
- Delegations go through the platform PDP (OPA in production) and are audited as
  `agent.delegate`.
- The run manager remembers where each agent's spec file is, so workers are found relative
  to it.

## Why
So the research-report reference app, and any team, can be started, streamed and inspected
like every other agent.

## How to check it
`uv run pytest services/agent-api/tests/test_team_api.py`
- The team run succeeds.
- The researcher, analyst and writer are each assembled with only their own tool.
- Three delegations are allowed and audited.

## Risks and follow-ups
Worker runs execute inside the supervisor's node. Their tool calls stream as events, but their
checkpoints are not stored separately.
