"""A slow or absent telemetry collector must never hold up a run (found by the Step 34 eval
harness: every team worker force-flushed the OTLP exporter, adding seconds per delegation)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from agent_compiler import team_eval, tracing
from agent_spec import load_spec
from opentelemetry.sdk.trace import TracerProvider

ROOT = Path(__file__).resolve().parents[3]


async def test_team_workers_never_flush_and_a_service_provider_is_never_flushed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flushed: list[Any] = []
    monkeypatch.setattr(
        tracing, "flush", lambda provider=None, timeout_millis=5000: flushed.append(provider)
    )
    path = ROOT / "agents" / "support-handoff" / "agent.yaml"
    result, workers = await team_eval.run_case(
        load_spec(path), path, {"task": "I want a refund for invoice 12"}, team_eval.respond
    )
    assert result.status.value == "succeeded"
    assert len(workers.models) >= 2, "the team delegated to workers"
    assert flushed == [], "neither the team (a caller's provider) nor its workers flush"


def test_only_the_cli_fallback_provider_is_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    fallback = TracerProvider()
    monkeypatch.setattr(tracing, "_provider", None)
    assert not tracing.owns(None), "nothing to flush before just run made its provider"
    monkeypatch.setattr(tracing, "_provider", fallback)
    assert tracing.owns(fallback)
    assert tracing.owns(None)
    assert not tracing.owns(TracerProvider()), "a service's provider exports in the background"
