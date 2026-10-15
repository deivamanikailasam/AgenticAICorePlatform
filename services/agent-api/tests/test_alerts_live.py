"""Step 30 "Done when": an alert fires on a test budget-trip spike and links to a runbook.

End to end on the stand-in: looping agents run through the run API and are stopped by loop
detection; the platform's metrics go over OTLP to the collector, Prometheus scrapes them and
evaluates ``agent-rules.yaml``; ``AgentBudgetTripSpike`` fires and reaches Alertmanager with its
``runbook_url``, which points to a runbook in this repository.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration

PROMETHEUS = os.environ.get("P0_PROMETHEUS_URL", "http://127.0.0.1:9090")
ALERTMANAGER = os.environ.get("P0_ALERTMANAGER_URL", "http://127.0.0.1:9093")
OTLP = os.environ.get("P0_OTEL_OTLP_ENDPOINT", "http://127.0.0.1:4318")
ROOT = Path(__file__).resolve().parents[3]

_spec = importlib.util.spec_from_file_location("budgets", Path(__file__).parent / "test_budgets.py")
assert _spec
assert _spec.loader
budgets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(budgets)


async def _poll(fetch: Any, within: float = 150.0) -> Any:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        found = await fetch()
        if found:
            return found
        await asyncio.sleep(3)
    return None


async def test_a_budget_trip_spike_fires_an_alert_that_links_to_its_runbook() -> None:
    from agent_telemetry import metrics as M  # noqa: N812
    from agent_telemetry.export import setup_metrics
    from opentelemetry import metrics

    setup_metrics("agent-api-alert-test", OTLP, interval_seconds=1)
    M.reset_for_tests()
    agent = f"looper-{uuid.uuid4().hex[:8]}"  # a fresh series, so earlier runs don't count
    for i in range(6):
        run, out = await budgets.run_looper(budgets.Looping(lambda i: "1+1"), name=agent)
        assert run.status == "stopped"
        assert "loop:" in out["result"]["stop_reason"]
        if i == 0:  # the agent's series exist (at zero for other limits) before the spike
            metrics.get_meter_provider().force_flush()  # type: ignore[attr-defined]
            await asyncio.sleep(12)  # export, then at least one scrape
    metrics.get_meter_provider().force_flush()  # type: ignore[attr-defined]

    async with httpx.AsyncClient(timeout=10) as http:

        async def firing() -> Any:
            alerts = (await http.get(f"{PROMETHEUS}/api/v1/alerts")).json()["data"]["alerts"]
            return next(
                (
                    a
                    for a in alerts
                    if a["labels"].get("alertname") == "AgentBudgetTripSpike"
                    and a["labels"].get("gen_ai_agent_name") == agent
                    and a["state"] == "firing"
                ),
                None,
            )

        alert = await _poll(firing)
        assert alert is not None, "AgentBudgetTripSpike did not fire in Prometheus"
        assert alert["labels"]["limit"] == "loop"
        assert alert["labels"]["severity"] == "page"

        async def delivered() -> Any:
            alerts = (await http.get(f"{ALERTMANAGER}/api/v2/alerts")).json()
            return next(
                (
                    a
                    for a in alerts
                    if a["labels"].get("alertname") == "AgentBudgetTripSpike"
                    and a["labels"].get("gen_ai_agent_name") == agent
                ),
                None,
            )

        received = await _poll(delivered, within=60)
    assert received is not None, "Alertmanager did not receive the alert"
    url = received["annotations"]["runbook_url"]
    prefix = "https://github.com/deivamanikailasam/AgenticAICorePlatform/blob/main/"
    assert url.startswith(prefix)
    runbook = ROOT / url.removeprefix(prefix)
    assert runbook.exists()
    assert "AgentBudgetTripSpike" in runbook.read_text()
    assert "runs stopped by loop" in received["annotations"]["summary"]
