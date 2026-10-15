"""``telemetry_emitter.otel``: the ``TelemetryEmitter`` contract on OpenTelemetry (Step 30)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from agent_contracts.interfaces import TelemetryEmitter
from opentelemetry import metrics
from opentelemetry.trace import Span

from agent_telemetry.spans import tracer


class OtelTelemetryEmitter(TelemetryEmitter):
    """Spans on the global tracer provider; ``metric`` records to a histogram per name."""

    def __init__(self, meter_name: str = "agent_platform") -> None:
        self._meter = metrics.get_meter(meter_name)
        self._histograms: dict[str, Any] = {}

    def span(
        self, name: str, attributes: Mapping[str, Any] | None = None
    ) -> AbstractContextManager[Any]:
        @contextmanager
        def _cm() -> Iterator[Span]:
            with tracer().start_as_current_span(
                name, attributes=dict(attributes or {}), record_exception=True
            ) as span:
                yield span

        return _cm()

    def metric(self, name: str, value: float, attributes: Mapping[str, Any] | None = None) -> None:
        if name not in self._histograms:
            self._histograms[name] = self._meter.create_histogram(name)
        self._histograms[name].record(value, dict(attributes or {}))
