"""Repository-wide pytest fixtures."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_EXPORTER = InMemorySpanExporter()


@pytest.fixture
def span_exporter() -> InMemorySpanExporter:
    """Spans recorded by the global tracer provider. OTel allows setting that provider only once
    per process, so every test shares this exporter; it is cleared before each test."""
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        trace.set_tracer_provider(provider)
    _EXPORTER.clear()
    return _EXPORTER
