# ADR 0041: Agent telemetry on the OpenTelemetry GenAI conventions; content only to Langfuse; SLOs, alerts and runbooks

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 30
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 30 asks for:
- spans that follow the OpenTelemetry GenAI conventions (`invoke_agent {agent}`, `chat` per model
  call, `execute_tool {tool}` per tool call) with token usage, model, tool and agent attributes;
- MCP spans that follow the MCP conventions;
- prompt and response content only in Langfuse, redacted and access-controlled, never in general
  logs;
- these metrics: runs, success, steps per run, tool error rate, approvals pending, cost per run
  and per tenant, p50/p95 latency, budget trips, guardrail hits;
- Grafana dashboards, SLOs, alert rules and runbooks.

Done when:
- one trace shows a multi-agent run end to end in Tempo and Langfuse;
- alerts fire on a test budget-trip spike and link to a runbook.

Before this step only `just run` (Step 4) emitted spans. The run API emitted none.

## Decision
1. **A `libs/agent-telemetry` library.**
   - Span wrappers around every model client and tool executor the assembly builds, plus an
     `invoke_agent` span per run in the run manager. Team workers run through `run()`, which now
     uses the same global provider, so a multi-agent run is one trace.
   - MCP client spans are `tools/call {tool}` (CLIENT, `mcp.method.name`, `server.address`),
     under the platform's `execute_tool`.
   - `telemetry_emitter.otel` implements the Step 2 contract.
2. **Pinned conventions.** They are in Development status and moved to
   `open-telemetry/semantic-conventions-genai`, which has no release yet, so they are pinned to a
   commit (`semconv.SEMCONV_COMMIT`). Provider name is `litellm` (a gateway, not a model vendor).
3. **Content goes one way.**
   - The opt-in content attributes are recorded only when a Langfuse exporter is configured, and
     only after redaction: secret shapes always, PII through Presidio when configured. If
     redaction fails, the content is withheld.
   - Every exporter except Langfuse is wrapped in `ContentFreeExporter`, which drops those
     attributes. Tempo and logs never receive content, whatever the configuration.
   - Langfuse's project membership is the access control. Its secret key comes from OpenBao.
4. **Metrics over OTLP.** They go to the collector, then to Prometheus. Convention names and
   bucket boundaries are used where defined (`gen_ai.invoke_agent.duration`,
   `gen_ai.execute_tool.duration`, `gen_ai.client.operation.duration`, token counters). The rest
   use the `agent_platform.` prefix. Labels are low-cardinality only.
5. **SLOs, alerts and runbooks as code.**
   - Recording rules and 7 alerts live in `agent-rules.yaml`, unit-tested with promtool.
   - `check_dashboards.py` checks that every query names a real metric, that the dashboard covers
     every required metric, and that every alert's runbook exists.
   - The dashboard is loaded through Grafana's API (`just provision-grafana`).
   - Objectives and thresholds are initial targets, stated as such (`docs/reference/slos.md`).
6. **Stand-in.** Prometheus, Alertmanager, Grafana and Langfuse (with ClickHouse) are added. CI
   proves both "Done when" checks end to end.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| OpenLLMetry / OpenInference auto-instrumentation | Instruments the libraries, not the platform's own concepts (runs, delegation, approvals, budgets); the platform already owns every model and tool call site |
| Content on spans everywhere, filtered in the collector | One collector misconfiguration away from prompts in Tempo; dropping content at the exporter in-process is fail-safe |
| Langfuse SDK instead of OTLP | Another instrumentation path; Langfuse ingests OTLP and maps the GenAI conventions natively |
| Arize Phoenix as the content store | Equal choice on OTLP; Langfuse is Project 0's (spec default). Phoenix is a configuration change: another OTLP target with content |
| Grafana-managed alerts | Prometheus rules can be unit-tested with promtool and are portable; Grafana alerting remains an option on Project 0 |

## Consequences
- **Prometheus edge cases.**
  - `increase()` can't see growth on a counter series born above zero. Budget-trip series are
    therefore created at zero for every limit at an agent's first run, and the first trip itself
    isn't counted (documented in `slos.md`).
  - Each API replica reports the pending approvals of its own runs.
- **Resumed runs.** A run resumed after an approval starts a new trace, linked by the
  conversation and run ids.
- **Langfuse v4.** It runs in "events_only" mode and reads through `/api/public/v2/observations`.
  Its web service needs about 2 GB of memory.
- **VERIFY-ON-MAC.**
  - How Project 0's Prometheus loads rule files.
  - Its collector's metrics pipeline.
  - Its Grafana data sources.
  - The Langfuse host, project, keys and membership.
