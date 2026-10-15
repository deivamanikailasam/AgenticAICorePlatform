"""Minimal OpenTelemetry setup for traced runs (Step 30 replaces this with agent-telemetry).

Span names and attributes follow the OpenTelemetry GenAI conventions where they exist
(``invoke_agent <name>``, ``gen_ai.operation.name``, ``gen_ai.agent.name``, ``gen_ai.agent.id``,
``gen_ai.conversation.id``); platform-specific attributes use the ``agent_platform.`` prefix.
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter

SERVICE = "agent-platform"
_provider: TracerProvider | None = None


def configure(endpoint: str | None = None, exporter: SpanExporter | None = None) -> TracerProvider:
    """Return the process-wide OTLP provider, or a private one wired to ``exporter`` (tests)."""
    global _provider
    if exporter is not None:
        private = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
        private.add_span_processor(SimpleSpanProcessor(exporter))
        return private
    if _provider is None:
        base = endpoint or os.environ.get("P0_OTEL_OTLP_ENDPOINT", "http://127.0.0.1:4318")
        _provider = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
        _provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces"))
        )
    return _provider


def tracer(provider: TracerProvider | None = None) -> trace.Tracer:
    return (provider or configure()).get_tracer("agent_platform.runtime")


def flush(provider: TracerProvider | None = None) -> None:
    (provider or configure()).force_flush()
