# Only approved agents run, or are delegated to, in staging and prod

**Step:** 25 · **Commit:** feat(agent-api): run and delegate only approved agents in staging and prod · **Date:** 2026-10-15

## What changed
- Compiler: `run()` and the team executors take an `admit` gate (`Admit`). Before a team
  delegates, the worker's spec goes through the gate and then through policy
  (`agent.delegate`). A refused worker is never built or run, and the round records "denied".
  The gate is passed down into nested teams.
- Run API:
  - `AGENT_ENV` (`dev` by default) is a new setting.
  - On start, the API syncs its specs into the registry and builds `ApprovedOnly`.
  - Starting a run of an agent that isn't admitted returns 403, with the reason.
  - Run contexts now carry `agent_version`.
- Compose: agent-api gets `AGENT_ENV` and a `/tmp` registry file for dev without a database.

## Why
Step 25's "Done when": a supervisor cannot delegate to a draft agent in staging or prod. Gating
run creation applies the same rule to the agents people start directly (ASI10, rogue agents).

## How to check it
`uv run pytest services/agent-api/tests/test_registry_gate.py`. The tests cover three cases:
- In staging, `research-report` with a draft writer: researcher and analyst run; the writer is
  refused, never assembled, and audited; the run ends `stopped`, not `succeeded`.
- A draft agent in prod returns 403.
- In dev, drafts run.

## Risks and follow-ups
- Runs already in progress are not stopped when their agent is deprecated. Stopping one agent
  version is the kill-switch drill in Step 38.
