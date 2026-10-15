# Service level objectives and alerts

These objectives and thresholds are **initial operating targets** chosen by the platform team.
They aren't measurements. Review them monthly against the **Agent platform** dashboard and change
them through a reviewed edit of `infra/observability/prometheus/agent-rules.yaml`. The promtool
tests (`just obs-test`) must pass after any change.

## Objectives

| SLO | Objective (28 days) | Measured by | Alert |
|---|---|---|---|
| Run availability | 99% of finished runs do not fail | `agent_platform:run_failure:ratio_rate5m` / `…_rate1h` | AgentRunFailureBudgetBurn (page) at 14.4× burn on 1 h and 5 m |
| Run latency | p95 run time per agent ≤ 120 s | `agent_platform:invoke_agent_duration_seconds:p95_5m` | AgentLatencyHigh (ticket) after 10 m |
| Tool reliability | ≤ 20% failing calls per tool | `agent_platform:tool_error:ratio_rate5m` | AgentToolErrorRateHigh (ticket) after 10 m |

"Failed" means the run ended in an error. These outcomes are not failures:
- **stopped:** a budget or loop limit was reached;
- **cancelled:** the run was cancelled, including when an approval expired;
- **taken over:** a person finished the run.

They are the platform working as designed, and they have their own signals below.

## Guard limits

| Signal | Threshold | Alert |
|---|---|---|
| Budget or loop trips per agent | ≥ 5 in 10 minutes | AgentBudgetTripSpike (page) |
| Guardrail blocks per guardrail and stage | ≥ 20 in 10 minutes | AgentGuardrailBlockSpike (page) |
| Approvals waiting per agent | > 20 for 30 minutes | AgentApprovalsBacklog (ticket) |
| Spend per tenant | > AUD 50 in an hour | AgentTenantCostHigh (ticket) |

Each alert's `runbook_url` points to [docs/runbooks](../runbooks/README.md).

## Known limits of the signals

- **A new agent's first trips.** Prometheus `increase()` can't see growth on a series whose first
  sample is already above zero. The platform therefore creates each agent's trip series at zero
  for every limit when its first run finishes. That first run's own trip, though, arrives in
  that first sample, so it is not counted.
- **Guardrail hit series.** These are created on the first hit. A burst that starts on a new
  guardrail and stage pair is under-counted by its first export.
- **Approvals pending.** Each API replica reports its own runs. Dashboards sum the replicas.
