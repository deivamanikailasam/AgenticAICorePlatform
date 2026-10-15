"""Agent metrics (Step 30). Names follow the GenAI conventions where they define one; the rest
use the ``agent_platform.`` prefix. All go through OpenTelemetry to Project 0's collector and on
to Prometheus (``agent_platform_runs_total``, ``gen_ai_invoke_agent_duration_seconds`` ...).

| Spec metric | Instrument |
|---|---|
| runs, success | ``agent_platform.runs`` (counter; ``status``) |
| steps per run | ``agent_platform.run.steps`` (histogram; model calls per run) |
| tool error rate | ``gen_ai.execute_tool.duration`` (histogram; ``error.type`` on failures) |
| approvals pending | ``agent_platform.approvals.pending`` (gauge) |
| cost per run and per tenant | ``agent_platform.run.cost`` (histogram, AUD) and ``agent_platform.cost`` (counter, AUD) |
| p50/p95 latency | ``gen_ai.invoke_agent.duration`` (histogram, s); model calls: ``gen_ai.client.operation.duration`` |
| budget trips | ``agent_platform.budget.trips`` (counter; ``limit``) |
| guardrail hits | ``agent_platform.guardrail.hits`` (counter; ``guardrail``, ``stage``, ``action``) |

Labels stay low-cardinality: agent, tenant, tool, model, status, limit; never run or user ids.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation

from agent_telemetry import semconv as S  # noqa: N812

# bucket boundaries from the conventions
OPERATION_BUCKETS = [
    0.01,
    0.02,
    0.04,
    0.08,
    0.16,
    0.32,
    0.64,
    1.28,
    2.56,
    5.12,
    10.24,
    20.48,
    40.96,
    81.92,
]
AGENT_BUCKETS = [0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 12.8, 25.6, 51.2, 102.4, 204.8, 409.6]
STEP_BUCKETS = [1, 2, 3, 5, 8, 13, 21, 34, 55]
COST_BUCKETS = [0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]

LIMIT = re.compile(r"^budget:\s*([a-z_]+)")


def budget_limit(stop_reason: str | None) -> str | None:
    """Which limit a stop reason names: ``max_cost_aud`` ... or ``loop`` / ``max_steps``."""
    if not stop_reason:
        return None
    if m := LIMIT.search(stop_reason):
        return m.group(1)
    if stop_reason.startswith("loop:"):
        return "loop"
    if "max_steps" in stop_reason:
        return "max_steps"
    return None


class AgentMetrics:
    """The platform's instruments, created once per meter provider."""

    def __init__(self, meter: metrics.Meter | None = None) -> None:
        m = meter or metrics.get_meter("agent_platform")
        self.runs = m.create_counter(
            "agent_platform.runs", unit="{run}", description="Finished runs by status"
        )
        self.run_steps = m.create_histogram(
            "agent_platform.run.steps",
            unit="{step}",
            description="Model calls per run",
            explicit_bucket_boundaries_advisory=STEP_BUCKETS,
        )
        self.run_cost = m.create_histogram(
            "agent_platform.run.cost",
            unit="{AUD}",
            description="Cost per run in AUD",
            explicit_bucket_boundaries_advisory=COST_BUCKETS,
        )
        self.cost = m.create_counter(
            "agent_platform.cost", unit="{AUD}", description="Spend in AUD"
        )
        self.budget_trips = m.create_counter(
            "agent_platform.budget.trips",
            unit="{trip}",
            description="Runs stopped by a budget or loop limit",
        )
        self.guardrail_hits = m.create_counter(
            "agent_platform.guardrail.hits",
            unit="{hit}",
            description="Guardrail findings by action",
        )
        self.agent_duration = m.create_histogram(
            "gen_ai.invoke_agent.duration",
            unit="s",
            description="End-to-end duration of an agent invocation",
            explicit_bucket_boundaries_advisory=AGENT_BUCKETS,
        )
        self.tool_duration = m.create_histogram(
            "gen_ai.execute_tool.duration",
            unit="s",
            description="Duration of a tool execution",
            explicit_bucket_boundaries_advisory=OPERATION_BUCKETS,
        )
        self.operation_duration = m.create_histogram(
            "gen_ai.client.operation.duration",
            unit="s",
            description="GenAI operation duration",
            explicit_bucket_boundaries_advisory=OPERATION_BUCKETS,
        )
        self.input_tokens = m.create_counter(
            "gen_ai.client.inference.usage.input_tokens",
            unit="{token}",
            description="Input tokens used",
        )
        self.output_tokens = m.create_counter(
            "gen_ai.client.inference.usage.output_tokens",
            unit="{token}",
            description="Output tokens used",
        )
        self._pending: Callable[[], Iterable[tuple[int, dict[str, Any]]]] = lambda: ()
        m.create_observable_gauge(
            "agent_platform.approvals.pending",
            callbacks=[self._observe_pending],
            unit="{approval}",
            description="Approvals waiting for a decision",
        )

    def _observe_pending(self, options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(v, a) for v, a in self._pending()]

    def pending_from(self, source: Callable[[], Iterable[tuple[int, dict[str, Any]]]]) -> None:
        """Where the pending-approvals gauge reads from: (count, attributes) pairs."""
        self._pending = source

    def run_finished(
        self,
        agent: str,
        tenant: str,
        status: str,
        seconds: float,
        steps: int,
        cost_aud: float,
        stop_reason: str | None,
    ) -> None:
        attrs = {S.AGENT_NAME: agent, S.TENANT_ID: tenant}
        self.runs.add(1, {**attrs, S.RUN_STATUS: status})
        self.run_steps.record(steps, {S.AGENT_NAME: agent})
        self.run_cost.record(cost_aud, attrs)
        if cost_aud:
            self.cost.add(cost_aud, attrs)
        duration = {S.AGENT_NAME: agent}
        if status == "failed":
            duration[S.ERROR_TYPE] = "run_failed"
        self.agent_duration.record(seconds, duration)
        if limit := budget_limit(stop_reason):
            self.budget_trips.add(1, {S.AGENT_NAME: agent, "limit": limit})

    def tool_finished(
        self, tool: str, agent: str, tool_type: str, seconds: float, error: str | None
    ) -> None:
        attrs = {S.TOOL_NAME: tool, S.AGENT_NAME: agent, S.TOOL_TYPE: tool_type}
        if error:
            attrs[S.ERROR_TYPE] = error
        self.tool_duration.record(seconds, attrs)

    def chat_finished(
        self,
        request_model: str,
        response_model: str | None,
        seconds: float,
        input_tokens: int,
        output_tokens: int,
        agent: str | None,
        error: str | None,
    ) -> None:
        attrs = {
            S.OPERATION: S.CHAT,
            S.PROVIDER: S.PROVIDER_LITELLM,
            S.REQUEST_MODEL: request_model,
        }
        if response_model:
            attrs[S.RESPONSE_MODEL] = response_model
        if agent:
            attrs[S.AGENT_NAME] = agent
        duration = {**attrs, S.ERROR_TYPE: error} if error else attrs
        self.operation_duration.record(seconds, duration)
        if not error:
            self.input_tokens.add(input_tokens, attrs)
            self.output_tokens.add(output_tokens, attrs)

    def guardrail_hit(self, guardrail: str, stage: str, action: str) -> None:
        self.guardrail_hits.add(1, {"guardrail": guardrail, "stage": stage, "action": action})


_metrics: AgentMetrics | None = None


def agent_metrics() -> AgentMetrics:
    """The process's instruments (on the global meter provider)."""
    global _metrics
    if _metrics is None:
        _metrics = AgentMetrics()
    return _metrics


def reset_for_tests(meter: metrics.Meter | None = None) -> AgentMetrics:
    global _metrics
    _metrics = AgentMetrics(meter)
    return _metrics
