"""Run a spec end to end: load → compile → adapter → traced execution."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent_contracts.dummies.runtime import BUILTIN_FUNCTIONS
from agent_contracts.models import (
    ExecutablePlan,
    NodeKind,
    RunContext,
    RunEventType,
    RunResult,
    RunStatus,
)
from agent_spec import AgentSpec, load_spec
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Span, Status, StatusCode

from agent_compiler import tracing
from agent_compiler.compiler import compile_spec, runtime_for


def resolve_function(name: str) -> Callable[[dict[str, Any]], Any]:
    """``hello`` (built in) or a dotted path ``package.module.func`` / ``package.module:func``."""
    if name in BUILTIN_FUNCTIONS:
        return BUILTIN_FUNCTIONS[name]
    module, _, attr = name.replace(":", ".").rpartition(".")
    if not module:
        raise LookupError(f"function '{name}' is not built in and is not a dotted path")
    fn = getattr(importlib.import_module(module), attr)
    if not callable(fn):
        raise LookupError(f"'{name}' is not callable")
    return fn  # type: ignore[no-any-return]


def functions_for(plan: ExecutablePlan) -> dict[str, Callable[[dict[str, Any]], Any]]:
    names = {str(n.config["fn"]) for n in plan.nodes if n.kind is NodeKind.FUNCTION}
    return {n: resolve_function(n) for n in names}


async def run(
    spec: AgentSpec,
    inputs: Mapping[str, Any],
    ctx: RunContext,
    provider: TracerProvider | None = None,
) -> tuple[RunResult, str]:
    """Execute ``spec`` and return the result and the trace id (hex)."""
    plan = compile_spec(spec)
    adapter = runtime_for(spec, functions=functions_for(plan))
    handle = await adapter.build(plan)
    tr = tracing.tracer(provider)
    attrs = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": spec.name,
        "gen_ai.agent.id": f"{spec.name}@{spec.version}",
        "gen_ai.conversation.id": ctx.thread_id,
        "agent_platform.run.id": ctx.run_id,
        "agent_platform.tenant.id": ctx.tenant_id,
        "agent_platform.risk_tier": spec.risk_tier.value,
        "agent_platform.plan.hash": str(plan.metadata["plan_hash"]),
        "agent_platform.runtime.adapter": spec.runtime.adapter,
    }
    with tr.start_as_current_span(f"invoke_agent {spec.name}", attributes=attrs) as root:
        trace_id = format(root.get_span_context().trace_id, "032x")
        open_spans: dict[str, Span] = {}
        async for event in adapter.stream(handle, inputs, ctx):
            if event.type is RunEventType.NODE_STARTED and event.node_id:
                open_spans[event.node_id] = tr.start_span(
                    f"execute_node {event.node_id}",
                    attributes={
                        "agent_platform.node.id": event.node_id,
                        "agent_platform.node.kind": plan.node(event.node_id).kind.value,
                    },
                )
            elif event.type is RunEventType.NODE_FINISHED and event.node_id in open_spans:
                span = open_spans.pop(event.node_id)
                span.set_attribute("agent_platform.node.outcome", str(event.data.get("outcome")))
                span.end()
            elif event.type is RunEventType.APPROVAL_REQUESTED and event.node_id in open_spans:
                open_spans.pop(event.node_id).end()
        for span in open_spans.values():
            span.end()
        state = await adapter.get_state(ctx.run_id)
        root.set_attribute("agent_platform.run.status", state.status.value)
        if state.status is RunStatus.FAILED:
            root.set_status(Status(StatusCode.ERROR, state.error or "failed"))
    result = RunResult(
        run_id=ctx.run_id,
        status=state.status,
        state=state,
        output=state.values.get(plan.exits[-1]) if plan.exits else None,
    )
    tracing.flush(provider)
    return result, trace_id


async def run_file(
    path: str | Path, inputs: Mapping[str, Any], ctx: RunContext
) -> tuple[RunResult, str]:
    return await run(load_spec(path), inputs, ctx)
