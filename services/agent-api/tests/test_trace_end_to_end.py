"""Step 30 "Done when": one trace shows a multi-agent run end to end in Tempo and Langfuse.

The research-report team (a supervisor and three workers) runs through the run API. Its spans go
to the collector and on to Tempo without content, and to Langfuse with content redacted at the
source. The same trace id is then read back from both:

* Tempo has every agent's ``invoke_agent`` span, the ``chat`` and ``execute_tool`` spans, and no
  prompt or response content;
* Langfuse has the same agents (AGENT observations), model calls (GENERATION) with redacted
  input: a credential in the user's task arrives as ``<AWS_ACCESS_KEY>``, never as itself.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_telemetry import content
from agent_telemetry import semconv as S  # noqa: N812
from agent_telemetry.export import ContentFreeExporter, LangfuseTarget
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

pytestmark = pytest.mark.integration

OTLP = os.environ.get("P0_OTEL_OTLP_ENDPOINT", "http://127.0.0.1:4318")
TEMPO = os.environ.get("P0_TEMPO_URL", "http://127.0.0.1:3200")
LANGFUSE = os.environ.get("LANGFUSE_HOST", "http://127.0.0.1:3100")
PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-standin")
SECRET_KEY = os.environ.get(
    "SECRET__LANGFUSE__AGENT_PLATFORM__SECRET_KEY", "standin-only-langfuse-secret"
)
LEAKED = "AKIA" + "Z7Q2" * 4  # an AWS-shaped access key id (not a real key)  # secret-scan: allow
AGENTS = {
    "research-report",
    "research-report-researcher",
    "research-report-analyst",
    "research-report-writer",
}

_spec = importlib.util.spec_from_file_location(
    "registry_gate", Path(__file__).parent / "test_registry_gate.py"
)
assert _spec
assert _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


class Switch(SpanProcessor):
    """Adds exporters to the test process's provider for this test only."""

    def __init__(self, inner: SpanProcessor) -> None:
        self.inner, self.on = inner, True

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        if self.on:
            self.inner.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        if self.on:
            self.inner.on_end(span)

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


@pytest.fixture
def exporters(span_exporter: InMemorySpanExporter) -> Iterator[list[Switch]]:
    from agent_api.telemetry import content_redactor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    target = LangfuseTarget(LANGFUSE, PUBLIC_KEY, SECRET_KEY)
    switches = [
        Switch(
            SimpleSpanProcessor(ContentFreeExporter(OTLPSpanExporter(endpoint=f"{OTLP}/v1/traces")))
        ),
        Switch(
            SimpleSpanProcessor(
                OTLPSpanExporter(endpoint=target.endpoint, headers=target.headers, timeout=30)
            )
        ),
    ]
    for s in switches:
        provider.add_span_processor(s)
    content.configure(True, content_redactor())  # what setup() does when Langfuse is configured
    yield switches
    content.configure(False, None)
    for s in switches:
        s.on = False


async def _poll(fetch: Any, within: float = 90.0) -> Any:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        found = await fetch()
        if found:
            return found
        await asyncio.sleep(3)
    return await fetch()


async def test_a_team_run_is_one_trace_in_tempo_and_langfuse(
    exporters: list[Switch], span_exporter: InMemorySpanExporter
) -> None:
    manager, app, _, _ = await gate.world("dev", [])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/v1/agents/research-report/runs",
            json={"input": {"task": f"Solar growth? My key is {LEAKED}"}},
            headers=gate.AUTH,
        )
    run = manager.runs[resp.json()["run_id"]]
    await manager.wait_idle(run)
    assert run.status == "succeeded", run.events[-1]
    for s in exporters:
        s.force_flush()
    root = next(
        s for s in span_exporter.get_finished_spans() if s.name == "invoke_agent research-report"
    )
    trace_id = format(root.context.trace_id, "032x")

    async with httpx.AsyncClient(timeout=15) as http:

        async def tempo() -> list[dict[str, Any]] | None:
            r = await http.get(f"{TEMPO}/api/traces/{trace_id}")
            if r.status_code != 200:
                return None
            spans = [
                s
                for batch in r.json().get("batches", r.json().get("resourceSpans", []))
                for scope in batch.get("scopeSpans", [])
                for s in scope.get("spans", [])
            ]
            names = {s["name"] for s in spans}
            return spans if {f"invoke_agent {a}" for a in AGENTS} <= names else None

        spans = await _poll(tempo)
        assert spans, f"trace {trace_id} not complete in Tempo"
        names = {s["name"] for s in spans}
        assert any(n.startswith("chat ") for n in names)
        assert "execute_tool search" in names
        attrs = {a["key"] for s in spans for a in s.get("attributes", [])}
        assert not attrs & S.CONTENT_ATTRIBUTES, "Tempo must not receive content"
        assert LEAKED not in json.dumps(spans)

        auth = (PUBLIC_KEY, SECRET_KEY)

        async def langfuse() -> list[dict[str, Any]] | None:
            r = await http.get(
                f"{LANGFUSE}/api/public/v2/observations",
                params={"traceId": trace_id, "fields": "core,basic,io", "limit": 100},
                auth=auth,
            )
            if r.status_code != 200:
                return None
            data = r.json()["data"]
            agents = {o["name"] for o in data if o["type"] == "AGENT"}
            return data if {f"invoke_agent {a}" for a in AGENTS} <= agents else None

        observations = await _poll(langfuse)
    assert observations, f"trace {trace_id} not complete in Langfuse"
    generations = [o for o in observations if o["type"] == "GENERATION"]
    assert generations, "model calls are GENERATION observations"
    inputs = " ".join(str(o.get("input")) for o in generations)
    assert "<AWS_ACCESS_KEY>" in inputs  # content is there, redacted
    assert LEAKED not in json.dumps(observations)
    assert all(o["traceId"] == trace_id for o in observations)
