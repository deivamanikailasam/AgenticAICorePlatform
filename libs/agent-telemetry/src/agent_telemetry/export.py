"""Where spans go (Step 30).

* Every span goes to the OpenTelemetry Collector (Project 0 → Tempo) **without** content:
  ``ContentFreeExporter`` drops the GenAI content attributes from spans and span events.
* When Langfuse is configured, spans also go to Langfuse's OTLP endpoint **with** content,
  which was redacted before it was recorded. Langfuse projects are access-controlled (only the
  project's members see traces), unlike the general trace store.

Content capture is switched on only together with a Langfuse exporter, so content can never
reach a general backend by configuration alone.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from agent_telemetry import content
from agent_telemetry.semconv import CONTENT_ATTRIBUTES


def strip_content(span: ReadableSpan) -> ReadableSpan:
    attrs = {k: v for k, v in (span.attributes or {}).items() if k not in CONTENT_ATTRIBUTES}
    events = [
        Event(
            e.name,
            {k: v for k, v in (e.attributes or {}).items() if k not in CONTENT_ATTRIBUTES},
            e.timestamp,
        )
        for e in span.events
    ]
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attrs,
        events=events,
        links=span.links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class ContentFreeExporter(SpanExporter):
    """Wraps an exporter for general backends (Tempo, logs): content attributes never pass."""

    def __init__(self, inner: SpanExporter) -> None:
        self.inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self.inner.export([strip_content(s) for s in spans])

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


@dataclass(frozen=True)
class LangfuseTarget:
    """Langfuse's OTLP endpoint (``<host>/api/public/otel``) and the project's key pair."""

    host: str
    public_key: str
    secret_key: str

    @property
    def endpoint(self) -> str:
        return f"{self.host.rstrip('/')}/api/public/otel/v1/traces"

    @property
    def headers(self) -> dict[str, str]:
        token = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()
        return {"Authorization": f"Basic {token}"}


def build_provider(
    service: str,
    general: SpanExporter | None,
    langfuse: SpanExporter | None = None,
    redactor: content.Redactor | None = None,
    batch: bool = True,
) -> TracerProvider:
    """A tracer provider with the general exporter (content stripped) and, optionally, the
    Langfuse exporter (content redacted at the source). Sets the process content policy."""
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    processor = BatchSpanProcessor if batch else SimpleSpanProcessor
    if general is not None:
        provider.add_span_processor(processor(ContentFreeExporter(general)))
    if langfuse is not None:
        provider.add_span_processor(processor(langfuse))
    content.configure(capture=langfuse is not None, redactor=redactor)
    return provider


def setup(
    service: str,
    otlp_endpoint: str | None,
    langfuse: LangfuseTarget | None = None,
    redactor: content.Redactor | None = None,
) -> TracerProvider:
    """Install the global tracer provider for a service process."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    general = (
        OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces")
        if otlp_endpoint
        else None
    )
    lf = (
        OTLPSpanExporter(endpoint=langfuse.endpoint, headers=langfuse.headers) if langfuse else None
    )
    provider = build_provider(service, general, lf, redactor)
    trace.set_tracer_provider(provider)
    if otlp_endpoint:
        setup_metrics(service, otlp_endpoint)
    return provider


def setup_metrics(service: str, otlp_endpoint: str, interval_seconds: float = 15.0) -> None:
    """Metrics to the collector over OTLP, every ``interval_seconds`` (the collector exposes
    them to Prometheus)."""
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/metrics"),
        export_interval_millis=int(interval_seconds * 1000),
    )
    metrics.set_meter_provider(
        MeterProvider(resource=Resource.create({"service.name": service}), metric_readers=[reader])
    )
