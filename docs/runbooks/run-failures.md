# AgentRunFailureBudgetBurn

**Severity:** page · **Fires when:** more than 14.4% of runs fail over both the last hour and the
last 5 minutes (the 99% availability SLO's error budget burning 14.4 times too fast).

## What it means
Runs are ending `failed` (errors, not budget stops or cancellations). Most often a shared
dependency is down: the LiteLLM gateway, a model, OPA, Keycloak, OpenBao, Postgres, or an MCP
server many agents use.

## Check
1. `just health`: every Project 0 dependency, OpenBao included.
2. Grafana → *Runs per minute by status* by agent: all agents (shared dependency) or one (that
   agent's tools or version)?
3. Traces of failed runs in Tempo: the failing span's `error.type` (`chat` spans: gateway or model;
   `execute_tool`: a tool; `tools/call`: an MCP server).
4. Audit log for `tool.call` with `error_code` and `policy.decision` denials (a policy bundle
   problem looks like a spike of denials).

## Fix
- Gateway or model down: Project 0's LiteLLM runbook; the platform's model circuit breaker stops
  hammering it. Fallback roles apply where configured.
- OPA down or policies missing: `just provision-policies`.
- OpenBao sealed or unreachable: tools that need credentials fail closed by design; restore OpenBao,
  then `just provision-openbao` (it recovers its database connection).
- One agent: roll back its version in the registry.

## Escalate
Project 0 on-call for its services; the agent's owner for a single agent. The page stays open
until the failure ratio is back under 1%.
