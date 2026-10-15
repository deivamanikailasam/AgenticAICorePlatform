# SLOs, alerts with runbooks, the Grafana dashboard; Prometheus, Alertmanager and Grafana in the stand-in

**Step:** 30 · **Commit:** feat(observability): SLOs, alert rules, runbooks and the Grafana dashboard · **Date:** 2026-10-15

## What changed
- **Rules.** `infra/observability/prometheus/agent-rules.yaml` holds 9 recording rules and 7 alerts:
  - recording rules for run failure and success ratios, p50/p95 run time, tool error ratio, average steps, cost per tenant and per run;
  - alerts AgentBudgetTripSpike, AgentRunFailureBudgetBurn (multi-window burn rate), AgentLatencyHigh, AgentToolErrorRateHigh, AgentApprovalsBacklog, AgentGuardrailBlockSpike and AgentTenantCostHigh.

  Every alert has a severity, a summary and a `runbook_url`.
- **Rule tests.** promtool unit tests in `infra/observability/prometheus-tests/`. `just obs-test` is now part of `just check`. It checks the rules, runs the promtool tests, and runs `scripts/check_dashboards.py`, which verifies:
  - every queried metric exists in code or as a recording rule;
  - the dashboard covers every Step 30 metric;
  - every alert's runbook exists, with its sections.
- **Runbooks.** `docs/runbooks/` has one page per alert, each in four parts: what it means, check, fix, escalate.
- **Dashboard.** `infra/observability/grafana/dashboards/agent-platform.json` has 12 panels: runs, success, p50/p95, steps, tool errors, approvals, cost per tenant and per run, budget trips, guardrail hits, model latency and tokens. `just provision-grafana`, now part of `just provision`, loads it through Grafana's API, idempotently.
- **Objectives.** `docs/reference/slos.md` sets out the objectives and guard limits, and says plainly that they are initial targets, not measurements.
- **Stand-in.** It now runs Prometheus v3.15.0, Alertmanager v0.34.1 and Grafana 13.2.3, each pinned by digest and bound to 127.0.0.1. The collector exposes metrics on :8889 for Prometheus to scrape.
- **Fix.** Budget-trip series are created at zero for each agent and limit. A stop reason with a node prefix (`work: loop: …`) is now recognised.

## Why
Step 30: "Grafana dashboards, SLOs, alert rules and runbooks." Done when: "alerts fire on a test budget-trip spike and link to a runbook".

## How to check it
- `just obs-test`
- `just test-integration services/agent-api/tests/test_alerts_live.py tests/scripts/test_observability_live.py`. The first test runs six looping runs through the run API, each stopped by loop detection. AgentBudgetTripSpike then fires in Prometheus, labelled with the agent and `limit=loop`. Alertmanager receives it, and its `runbook_url` points to `docs/runbooks/budget-trip-spike.md`, which exists. The second test checks:
  - dashboard provisioning is idempotent;
  - every panel query is answered by Prometheus;
  - all rules are healthy.

## Risks and follow-ups
- VERIFY-ON-MAC:
  - how Project 0's Prometheus loads rule files (mount `infra/observability/prometheus/`);
  - that its collector has a metrics pipeline to Prometheus;
  - the names of its Grafana data sources.
- Docker Hub rate-limited this container. The images were pulled through `mirror.gcr.io` by the same digest, and compose still references `docker.io`.
