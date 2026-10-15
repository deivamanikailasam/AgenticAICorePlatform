"""Step 30 against the stand-in (Project 0 on the Mac): the dashboards load into Grafana through
`just provision-grafana`, every panel's query is valid PromQL that Prometheus answers, and every
recording and alerting rule evaluates without error."""

import json
import os
from pathlib import Path

import httpx
import pytest

from scripts.provision import grafana

pytestmark = pytest.mark.integration

PROMETHEUS = os.environ.get("P0_PROMETHEUS_URL", "http://127.0.0.1:9090")
GRAFANA = os.environ.get("P0_GRAFANA_URL", "http://127.0.0.1:3000")
DASHBOARD = (
    Path(__file__).resolve().parents[2]
    / "infra/observability/grafana/dashboards/agent-platform.json"
)


def test_dashboards_provision_idempotently() -> None:
    auth = (os.environ["GRAFANA_ADMIN_USER"], os.environ["GRAFANA_ADMIN_PASSWORD"])
    with httpx.Client(timeout=15, auth=auth) as c:
        grafana.provision(c, GRAFANA)
        again = grafana.provision(c, GRAFANA)
        stored = c.get(f"{GRAFANA}/api/dashboards/uid/agent-platform").json()
    assert all(a.endswith("exists") for a in again), again
    assert stored["meta"]["folderTitle"] == "Agent platform"
    assert len(stored["dashboard"]["panels"]) == len(json.loads(DASHBOARD.read_text())["panels"])


def test_every_panel_query_is_answered_by_prometheus() -> None:
    panels = json.loads(DASHBOARD.read_text())["panels"]
    with httpx.Client(timeout=15) as c:
        for panel in panels:
            for target in panel["targets"]:
                expr = target["expr"].replace("$agent", ".*")
                resp = c.get(f"{PROMETHEUS}/api/v1/query", params={"query": expr})
                assert resp.status_code == 200, (panel["title"], resp.text)
                assert resp.json()["status"] == "success"


def test_rules_are_loaded_and_healthy() -> None:
    groups = httpx.get(f"{PROMETHEUS}/api/v1/rules", timeout=15).json()["data"]["groups"]
    rules = {r["name"]: r for g in groups for r in g["rules"]}
    assert {
        "AgentBudgetTripSpike",
        "AgentRunFailureBudgetBurn",
        "agent_platform:run_failure:ratio_rate5m",
    } <= set(rules)
    broken = {n: r.get("lastError") for n, r in rules.items() if r.get("health") == "err"}
    assert not broken, broken
