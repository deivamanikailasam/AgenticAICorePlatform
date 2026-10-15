"""Versions and trace ids on every audit event (Step 31).

``StampingAuditSink`` wraps any sink. The assembly registers each agent's versions when it builds
the agent; every event of that agent then carries them, and the trace id of the OpenTelemetry span
it was written under, so an auditor can go from an audit record to the exact spec, plan, prompts,
models and tools that ran, and to the trace in Tempo and Langfuse.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from agent_contracts.interfaces import AuditSink
from agent_contracts.models import AuditEvent, AuditRecord, ExecutablePlan
from opentelemetry import trace


def spec_digest(spec: Any) -> str:
    body = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def agent_versions(
    spec: Any,
    plan: ExecutablePlan | None,
    models: Mapping[str, str],
    tools: Mapping[str, str],
) -> dict[str, Any]:
    """What ran for this agent: spec version and hash, plan hash, prompt refs by node, the model
    alias each role resolves to, and each tool's version."""
    graph = getattr(spec, "graph", None)
    nodes = graph.nodes if graph is not None else ()
    prompts = {n.id: n.prompt for n in nodes if n.prompt}
    return {
        "spec": spec.version,
        "spec_hash": spec_digest(spec),
        "plan_hash": str(plan.metadata.get("plan_hash")) if plan is not None else None,
        "prompts": prompts,
        "models": dict(sorted(models.items())),
        "tools": dict(sorted(tools.items())),
    }


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


class StampingAuditSink(AuditSink):
    def __init__(self, inner: AuditSink) -> None:
        self.inner = inner
        self._versions: dict[str, dict[str, Any]] = {}
        # the last trace of each run: events written outside its spans (an approval decided
        # over HTTP, the approval filed after the run paused) join the run's trace
        self._traces: dict[str, str] = {}

    def register(self, agent: str, versions: Mapping[str, Any]) -> None:
        self._versions[agent] = dict(versions)

    def versions(self, agent: str) -> dict[str, Any]:
        return dict(self._versions.get(agent, {}))

    async def append(self, event: AuditEvent) -> AuditRecord:
        update: dict[str, Any] = {}
        if not event.versions and event.ctx.agent_id in self._versions:
            update["versions"] = self._versions[event.ctx.agent_id]
        root = event.ctx.run_id.split("/")[0]  # team workers' runs are <run>/<worker>-<n>
        if event.trace_id is None:
            found = event.ctx.trace_id or current_trace_id()
            if found:
                self._traces[root] = found
            update["trace_id"] = found or self._traces.get(root)
        return await self.inner.append(event.model_copy(update=update) if update else event)

    async def read(self, run_id: str | None = None) -> Sequence[AuditRecord]:
        return await self.inner.read(run_id)

    async def verify(self) -> bool:
        return await self.inner.verify()
