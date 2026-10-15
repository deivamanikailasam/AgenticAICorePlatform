# Step 30 · Agent telemetry, metrics and SLOs

## 1. At a glance

| | |
|---|---|
| **Goal** | See what every agent did, how well the platform is doing, and get woken up, with instructions, when it isn't |
| **Time** | About two days |
| **You end up with** | `libs/agent-telemetry` (GenAI spans, content policy, metrics); MCP spans on the MCP conventions; prompt content only in Langfuse, redacted; Prometheus rules (SLOs and 7 alerts, promtool-tested); runbooks; the **Agent platform** Grafana dashboard; Prometheus, Alertmanager, Grafana and Langfuse in the stand-in |
| **New words** | span, trace, semantic conventions, Langfuse, metric, histogram, p95, SLO, error budget, burn rate, alert, runbook |

## 2. The big idea

Three kinds of signal, each with its own job:
- **Traces** answer "what happened in this run?". Each step is a span, nested: the agent, its
  model calls, its tool calls, the workers it delegated to.
- **Metrics** answer "how are we doing overall?": runs, failures, time, cost, approvals waiting,
  limits hit, guardrails firing.
- **Alerts** turn a bad metric into a person being told, with a runbook saying what to do.

The words people type, and the answers the model gives, are sensitive. They go to one place only,
Langfuse, redacted and visible to project members. Everything else sees the shape of the work,
never the words.

> *Analogy:* an airline.
> - Each flight's black box is the **trace**. The departure board's on-time and cancellation
>   figures are the **metrics**.
> - When cancellations spike, the duty manager's pager goes off and they open the procedure
>   binder: the **alert** and the **runbook**.
> - Passenger conversations are not on the departure board. Only the investigators can read the
>   cockpit recording, and the names in it are blacked out.

## 3. What we're doing

1. Emit spans named and shaped by the OpenTelemetry GenAI conventions, for every agent, model call
   and tool call in the run API, workers included, as one trace.
2. Keep content off every general exporter, and send it, redacted, to Langfuse only.
3. Record the metrics Step 30 lists, and send them through the collector to Prometheus.
4. Write SLOs, alert rules with runbook links, and the dashboard, all tested.
5. Prove both "Done when" checks end to end on the stand-in.

## 4. Why we're doing this

Agents decide their own steps, so "it was slow" or "it cost too much" can't be explained without
a per-step record. Standard conventions mean any tool (Tempo, Langfuse, Phoenix, Grafana) can read
the traces. Alerts without runbooks wake people up with nothing to act on.

> *Watch out:* telemetry is a classic leak path. Prompts end up in log search, traces get shared
> in tickets. Here the default is "no content", and content goes one way only.

## 5. Key ideas

- **Span names and attributes.**
  - `invoke_agent {agent}`: agent name, id and version, conversation, run, tenant, status, tokens,
    AUD cost.
  - `chat {model}`: provider `litellm`, request and response model, tokens, finish reason, error
    type.
  - `execute_tool {tool}`: tool name, call id, type, the agent, error type.
  - MCP calls: `tools/call {tool}` under the platform's `execute_tool`.
  - The conventions are pinned to a commit, because they're still in Development.
- **One trace for a team.** Workers' `invoke_agent` spans sit under the supervisor's. Each model
  and tool call sits under the agent that made it.
- **Content policy.**
  - Content is captured only when Langfuse is configured, and redacted first: secrets always,
    personal data via Presidio.
  - If redaction is unavailable, the content is withheld.
  - Every other exporter strips content regardless of configuration.
- **Metrics.** Convention names where they exist (`gen_ai.invoke_agent.duration` …), and
  `agent_platform.*` for the rest. Labels never include run or user ids.
- **SLOs and burn rate.**
  - 99% of runs must not fail. "Burn rate" is how fast failures use up the allowed 1%.
  - Paging needs both the 1-hour and the 5-minute windows burning 14.4× too fast, so a single
    blip doesn't wake anyone.
- **Runbooks.** Every alert links to a page with four parts: what it means, check, fix, escalate.
  A check fails the build if a page is missing.

## 6. How to do it

```bash
just obs-test                         # promtool rule tests + dashboard/runbook checks (in just check)
just provision-grafana                # dashboard into Grafana, folder "Agent platform"
# services: spans and metrics to the collector; to Langfuse when configured
P0_OTEL_OTLP_ENDPOINT=http://otel-collector:4318
LANGFUSE_HOST=https://langfuse.example  LANGFUSE_PUBLIC_KEY=pk-lf-…   # secret key from OpenBao
```

Reading one run:
- In Tempo, search `gen_ai.agent.name=<agent>` or the run id. You see the timing of every step,
  without content.
- In Langfuse, open the same trace id. Agents show as AGENT, model calls as GENERATION, with the
  redacted input and output.

## 7. Benefits

- Any run, including multi-agent runs, can be followed step by step, with cost and tokens per
  step.
- Operators see success, latency, cost per tenant, approvals waiting, limits hit and guardrail
  blocks at a glance.
- Alerts arrive with a runbook link. The rules are unit-tested like code.
- Prompt content can't reach general logs by mistake.

## 8. Drawbacks and risks

- **Small counting gaps.** A new agent's very first trip, and the first export of a new guardrail
  series, aren't counted. This is a Prometheus limitation, documented in `slos.md`.
- **Resumed runs.** A run resumed after an approval starts a new trace, linked by conversation and
  run id.
- **Initial targets.** The thresholds and objectives are starting points; tune them from real
  data.
- **Langfuse costs.** Langfuse needs ClickHouse and about 2 GB of memory for its web service.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Langfuse for content | Arize Phoenix (OTLP) | Your team already runs Phoenix; it's another OTLP target with content |
| Platform-owned instrumentation | OpenLLMetry / OpenInference auto-instrumentation | Agents written outside the platform that you can't wrap |
| Prometheus rules | Grafana-managed alerts | Alerting is centralised in Grafana and promtool tests aren't needed |
| Multi-window burn-rate paging | Static error thresholds | Very low traffic, where ratios swing too much |

## 10. Which approach fits which situation

- **Debugging one bad run:** the Tempo trace for timing and where it failed, then Langfuse for
  what the model saw and said (redacted).
- **"Are we OK?":** the **Agent platform** dashboard: success ratio, p95, tool errors, approvals.
- **Money:** cost per tenant and per run, budget trips, and `just cost-reconcile` (Step 26).
- **Attacks:** guardrail hits and the guardrail-blocks runbook, then the audit log for the runs.

## 11. Common mistakes

- Logging prompts "temporarily" in a service log. Use Langfuse; it redacts.
- Putting run ids or user ids in metric labels. That's millions of series. Use traces for
  per-run detail.
- Raising a budget limit to silence AgentBudgetTripSpike without finding the loop.
- Alert rules with no runbook. The build now refuses them.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| One trace shows a multi-agent run end to end in Tempo and Langfuse | ✅ (stand-in) | `test_trace_end_to_end.py`: the research-report team (supervisor and 3 workers) via the run API. Tempo has all four `invoke_agent` spans, the `chat` spans and `execute_tool search` under the same trace id, with no content attributes. Langfuse 4.48.0 has the four agents as AGENT and the model calls as GENERATION, with input redacted (`<AWS_ACCESS_KEY>` where the task held a key; the key appears nowhere) |
| Alerts fire on a test budget-trip spike and link to a runbook | ✅ (stand-in) | `test_alerts_live.py`: 6 looping runs via the run API are stopped by loop detection. The metrics go collector → Prometheus. AgentBudgetTripSpike (`limit=loop`, severity page) fires and reaches Alertmanager. Its `runbook_url` points to `docs/runbooks/budget-trip-spike.md`, which exists |
| Spans follow the GenAI conventions; MCP spans the MCP conventions | ✅ | `test_spans.py` (kinds, names, attributes, errors, streaming); `test_telemetry.py` (a team run is one trace, each call under its agent); MCP `tools/call {tool}` CLIENT spans in `test_mcp.py` |
| Content only to Langfuse, redacted, never to general logs | ✅ | Content tests: redacted, sent only to the Langfuse exporter, stripped by `ContentFreeExporter`, withheld when redaction fails; capture is on only with Langfuse |
| Metrics listed in the spec | ✅ | `test_metrics.py`; names verified live in Prometheus |
| Dashboards, SLOs, alert rules, runbooks | ✅ | 12-panel dashboard provisioned idempotently and every query answered by Prometheus; 9 recording rules and 7 alerts, promtool-tested and healthy live; 7 runbooks; `docs/reference/slos.md` |
| ADR | ✅ | ADR 0041 |

## 13. Recap

Every run now leaves a trace in the standard GenAI shape:
- the agent, its model calls, its tool calls and its workers, in one tree;
- the words go only to Langfuse, redacted.

Metrics flow to Prometheus. The dashboard shows how the platform is doing, and the SLOs and alerts
say when it isn't. Each alert carries a runbook and is tested like code. Both checks the spec asks
for pass end to end on the stand-in.
