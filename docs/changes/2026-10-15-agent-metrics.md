# Agent metrics: runs, success, steps, tool errors, approvals, cost, latency, trips, guardrail hits

**Step:** 30 · **Commit:** feat(agent-telemetry): agent metrics over OpenTelemetry · **Date:** 2026-10-15

## What changed
- `agent_telemetry.metrics.AgentMetrics` provides every metric Step 30 lists. GenAI convention names and bucket boundaries are used where the conventions define them:
  - **Runs and success:** `agent_platform.runs`, by agent, tenant and status.
  - **Steps per run:** `agent_platform.run.steps`, counting model calls.
  - **Tool error rate:** `gen_ai.execute_tool.duration`, with `error.type` set on failures.
  - **Approvals pending:** `agent_platform.approvals.pending`, a gauge per agent read from the run manager.
  - **Cost per run and per tenant:** `agent_platform.run.cost` and `agent_platform.cost`, in AUD.
  - **p50/p95 latency:** `gen_ai.invoke_agent.duration`, plus `gen_ai.client.operation.duration` for model calls.
  - **Token usage:** `gen_ai.client.inference.usage.input_tokens` and `gen_ai.client.inference.usage.output_tokens`.
  - **Budget trips:** `agent_platform.budget.trips`, with `limit` naming the budget or loop limit.
  - **Guardrail hits:** `agent_platform.guardrail.hits`, by guardrail, stage and action.
- Where metrics are recorded:
  - the tracing wrappers record model and tool metrics;
  - the run manager records each finished run;
  - the guardrail pipeline records its hits.
- `setup()` also installs an OTLP metrics exporter to the collector, every 15 s.
- Labels stay low-cardinality: never run ids or user ids.

## Why
Step 30: "Metrics: runs, success, steps per run, tool error rate, approvals pending, cost per run and per tenant, p50/p95 latency, budget trips, guardrail hits."

## How to check it
`uv run pytest libs/agent-telemetry/tests/test_metrics.py`. It checks:
- status, steps, cost per tenant and latency buckets;
- a budget trip counted with `limit=max_cost_aud`;
- tool errors labelled `not_found`;
- the pending-approvals gauge;
- an injection blocked by the rules detector counted as a guardrail hit.

## Risks and follow-ups
Each API replica reports the pending approvals of its own runs, so dashboards sum them across replicas.
