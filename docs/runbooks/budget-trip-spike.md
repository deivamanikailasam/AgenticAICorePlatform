# AgentBudgetTripSpike

**Severity:** page · **Fires when:** one agent has 5 or more runs stopped by the same budget or
loop limit within 10 minutes.

## What it means
Runs of this agent keep hitting a hard limit (`limit` label): cost, tokens, tool calls, wall
clock, steps, or loop detection. Usually one of:
- a new agent or prompt version that loops or over-calls a tool;
- a tool or model that now returns something the agent can't use, so it keeps retrying;
- a burst of hostile input (for example a document that keeps the agent busy);
- limits set too tight for a legitimate change in workload.

The limits are doing their job: money is not being lost. The question is why.

## Check
1. Grafana → **Agent platform** → *Budget trips* and *Steps per run*: which agent, which limit,
   since when.
2. Was anything deployed? `just agents` (registry state and versions) and the Git log of
   `agents/<name>/`.
3. Open a stopped run's trace in Tempo (filter `gen_ai.agent.name`, `agent_platform.run.status=stopped`):
   repeated `execute_tool` spans with the same tool point to a loop; long `chat` spans point to
   the model.
4. The run record's `stop_reason` and partial output (`GET /v1/runs/{id}`) name the limit and show
   how far it got.
5. Guardrail hits at the same time suggest hostile input.

## Fix
- A bad version: deprecate it in the registry (staging/prod run approved versions only) and
  roll back to the previous approved one.
- A failing tool: see [tool-errors.md](tool-errors.md); the circuit breaker should already be
  limiting calls.
- A legitimate workload change: raise the specific limit in the agent's spec (`budgets`) through
  a reviewed change. Never raise limits to silence the alert without knowing the cause.

## Escalate
To the agent's owner (`owner` in its spec) at once. If spend is also climbing for a tenant, see
[cost.md](cost.md). If the cause looks like an attack, involve security.
