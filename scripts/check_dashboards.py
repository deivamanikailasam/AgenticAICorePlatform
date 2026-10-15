"""Checks the observability config against the code (Step 30), as part of ``just obs-test``.

* Every metric a dashboard panel or a rule queries exists: it is a recording rule in
  ``agent-rules.yaml`` or a Prometheus name of an instrument in ``agent_telemetry.metrics``.
* The dashboard shows every metric Step 30 lists.
* Every alert has a summary, a severity and a ``runbook_url`` pointing to a runbook in
  ``docs/runbooks/`` that exists and has the sections an on-call engineer needs.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "infra" / "observability" / "prometheus" / "agent-rules.yaml"
DASHBOARDS = ROOT / "infra" / "observability" / "grafana" / "dashboards"
RUNBOOKS = ROOT / "docs" / "runbooks"
RUNBOOK_BASE = "https://github.com/deivamanikailasam/AgenticAICorePlatform/blob/main/docs/runbooks/"
RUNBOOK_SECTIONS = ("## What it means", "## Check", "## Fix", "## Escalate")

# OpenTelemetry instrument → Prometheus names (the collector's prometheus exporter adds the
# unit suffix for seconds and ``_total`` for counters; ``{...}`` units add nothing)
INSTRUMENTS: dict[str, str] = {
    "agent_platform.runs": "counter",
    "agent_platform.run.steps": "histogram",
    "agent_platform.run.cost": "histogram",
    "agent_platform.cost": "counter",
    "agent_platform.budget.trips": "counter",
    "agent_platform.guardrail.hits": "counter",
    "agent_platform.approvals.pending": "gauge",
    "gen_ai.invoke_agent.duration": "histogram_seconds",
    "gen_ai.execute_tool.duration": "histogram_seconds",
    "gen_ai.client.operation.duration": "histogram_seconds",
    "gen_ai.client.inference.usage.input_tokens": "counter",
    "gen_ai.client.inference.usage.output_tokens": "counter",
}

# Step 30's list → a name the dashboard must query for it
REQUIRED = {
    "runs": "agent_platform_runs_total",
    "success": "agent_platform:run_success:ratio_rate1h",
    "steps per run": "agent_platform:run_steps:avg_1h",
    "tool error rate": "agent_platform:tool_error:ratio_rate5m",
    "approvals pending": "agent_platform_approvals_pending",
    "cost per run": "agent_platform:cost_aud:per_run_avg_1h",
    "cost per tenant": "agent_platform:cost_aud:per_tenant_1h",
    "p50/p95 latency": "agent_platform:invoke_agent_duration_seconds:p95_5m",
    "budget trips": "agent_platform_budget_trips_total",
    "guardrail hits": "agent_platform_guardrail_hits_total",
}

METRIC = re.compile(r"\b((?:agent_platform|gen_ai)[a-zA-Z0-9_:]*)")
LABELS = re.compile(r"\{[^}]*\}|\b(?:by|without)\s*\([^)]*\)")


def prometheus_names() -> set[str]:
    names: set[str] = set()
    for otel, kind in INSTRUMENTS.items():
        base = otel.replace(".", "_")
        if kind == "counter":
            names.add(f"{base}_total")
        elif kind == "gauge":
            names.add(base)
        else:
            base += "_seconds" if kind == "histogram_seconds" else ""
            names |= {f"{base}_bucket", f"{base}_sum", f"{base}_count"}
    return names


def _exprs(obj: Any) -> list[str]:
    if isinstance(obj, dict):
        return ([obj["expr"]] if isinstance(obj.get("expr"), str) else []) + [
            e for v in obj.values() for e in _exprs(v)
        ]
    if isinstance(obj, list):
        return [e for v in obj for e in _exprs(v)]
    return []


def check() -> list[str]:
    problems: list[str] = []
    rules = yaml.safe_load(RULES.read_text())
    recorded = {r["record"] for g in rules["groups"] for r in g["rules"] if "record" in r}
    known = prometheus_names() | recorded

    def unknown(expr: str, where: str) -> None:
        for name in METRIC.findall(LABELS.sub(" ", expr)):
            if name not in known:
                problems.append(f"{where}: unknown metric {name}")

    for g in rules["groups"]:
        for r in g["rules"]:
            unknown(r["expr"], f"rule {r.get('record') or r.get('alert')}")
            if "alert" not in r:
                continue
            ann, labels = r.get("annotations", {}), r.get("labels", {})
            if not ann.get("summary") or labels.get("severity") not in ("page", "ticket"):
                problems.append(f"alert {r['alert']}: needs a summary and severity page|ticket")
            url = str(ann.get("runbook_url", ""))
            if not url.startswith(RUNBOOK_BASE):
                problems.append(f"alert {r['alert']}: runbook_url must point to {RUNBOOK_BASE}")
                continue
            book = RUNBOOKS / url.removeprefix(RUNBOOK_BASE)
            if not book.exists():
                problems.append(f"alert {r['alert']}: runbook {book.name} does not exist")
                continue
            text = book.read_text()
            missing = [s for s in RUNBOOK_SECTIONS if s not in text]
            if missing or r["alert"] not in text:
                problems.append(f"runbook {book.name}: needs {missing} and the alert name")

    for path in sorted(DASHBOARDS.glob("*.json")):
        dash = json.loads(path.read_text())
        exprs = _exprs(dash.get("panels", []))
        for expr in exprs:
            unknown(expr, f"dashboard {path.name}")
        shown = " ".join(exprs)
        for what, name in REQUIRED.items():
            if name not in shown:
                problems.append(f"dashboard {path.name}: no panel for {what} ({name})")
    return problems


def main() -> int:
    problems = check()
    for p in problems:
        print(f"dashboards: {p}")
    print(f"dashboards: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
