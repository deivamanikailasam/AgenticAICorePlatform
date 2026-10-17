# Runbooks

One page per alert in `infra/observability/prometheus/agent-rules.yaml`. Each alert's
`runbook_url` points here, and `just obs-test` fails if a page is missing.

| Alert | Severity | Runbook |
|---|---|---|
| AgentBudgetTripSpike | page | [budget-trip-spike.md](budget-trip-spike.md) |
| AgentRunFailureBudgetBurn | page | [run-failures.md](run-failures.md) |
| AgentGuardrailBlockSpike | page | [guardrail-blocks.md](guardrail-blocks.md) |
| AgentLatencyHigh | ticket | [latency.md](latency.md) |
| AgentToolErrorRateHigh | ticket | [tool-errors.md](tool-errors.md) |
| AgentApprovalsBacklog | ticket | [approvals-backlog.md](approvals-backlog.md) |
| AgentTenantCostHigh | ticket | [cost.md](cost.md) |
| AgentReleaseRolledBack | ticket | [release-rollback.md](release-rollback.md) |

Drills: [dr-restore.md](dr-restore.md) restores the agent database (rehearsed by `just dr-drill`).

Actions and apps (no alert of their own):
- [kill-switch.md](kill-switch.md): stop an agent, a tool or a tenant, and turn it back on.
- [reference-apps.md](reference-apps.md): operating the three reference applications (Step 40).

Every page has the same four parts: what it means, what to check, how to fix it, and when to
escalate. The dashboard is **Agent platform** in Grafana; traces are in Tempo (no prompt content)
and Langfuse (redacted content, project members only).
