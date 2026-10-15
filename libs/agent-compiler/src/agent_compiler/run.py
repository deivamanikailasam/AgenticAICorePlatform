"""Run a spec end to end: load → compile → adapter → traced execution.

This is the composition root for runs: it resolves the spec's functions and plugs in the node
executors (Step 12) — LLM and router nodes through the platform model client, tool nodes through
the caller's (authorising) tool executor, sub-workflows compiled from the referenced spec and run
on the same adapter as child runs.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent_contracts.dummies.runtime import BUILTIN_FUNCTIONS
from agent_contracts.interfaces import (
    AgentLoop,
    Critic,
    Embedder,
    ModelClient,
    Planner,
    Router,
    RuntimeAdapter,
    ToolExecutor,
    ToolRegistry,
)
from agent_contracts.models import (
    ExecutablePlan,
    NodeKind,
    PlanNode,
    RunContext,
    RunEventType,
    RunResult,
    RunStatus,
)
from agent_contracts.registry import registry as component_registry
from agent_runtime.agent_loop import AgentLoopExecutor
from agent_runtime.agentic_rag import AgenticRagStrategy
from agent_runtime.executors import (
    LlmNodeExecutor,
    RouterNodeExecutor,
    SubplanNodeExecutor,
    ToolNodeExecutor,
)
from agent_runtime.models.gateway import GatewayModelClient
from agent_runtime.nodes import NodeExecutor, NodeRunner
from agent_runtime.strategies import (
    AgentNodeDispatcher,
    EvaluatorOptimiserStrategy,
    PlanExecuteStrategy,
    ReflectionStrategy,
    RewooStrategy,
)
from agent_spec import AgentSpec, load_spec
from agent_tools.assemble import tool_names
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Span, Status, StatusCode

from agent_compiler import tracing
from agent_compiler.compiler import compile_spec, runtime_for

FUNCTION_GROUP = "agent_platform.functions"


def resolve_function(name: str) -> Callable[[dict[str, Any]], Any]:
    """``hello`` (built in), a name an installed package registers under the
    ``agent_platform.functions`` entry-point group (``invoice.validate``), or a dotted path
    ``package.module.func`` / ``package.module:func``."""
    if name in BUILTIN_FUNCTIONS:
        return BUILTIN_FUNCTIONS[name]
    from importlib.metadata import entry_points

    registered = {ep.name: ep for ep in entry_points(group=FUNCTION_GROUP)}
    if name in registered:
        fn = registered[name].load()
        if not callable(fn):
            raise LookupError(f"registered function '{name}' is not callable")
        return fn  # type: ignore[no-any-return]
    module, _, attr = name.replace(":", ".").rpartition(".")
    if not module:
        raise LookupError(f"function '{name}' is not built in and is not a dotted path")
    fn = getattr(importlib.import_module(module), attr)
    if not callable(fn):
        raise LookupError(f"'{name}' is not callable")
    return fn  # type: ignore[no-any-return]


def functions_for(plan: ExecutablePlan) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Every ``fn`` in the plan: function nodes, routers with a function, compensation steps and
    validation gates."""
    names = {str(n.config["fn"]) for n in plan.nodes if n.config.get("fn")}
    for n in plan.nodes:
        gate = (n.config.get("config") or {}).get("gate") or {}
        if isinstance(gate, Mapping) and gate.get("fn"):
            names.add(str(gate["fn"]))
    return {n: resolve_function(n) for n in names}


NEEDS_MODEL = {NodeKind.LLM, NodeKind.ROUTER, NodeKind.AGENT}


def executors_for(
    spec: AgentSpec,
    plan: ExecutablePlan,
    functions: Mapping[str, Callable[[dict[str, Any]], Any]],
    *,
    model: ModelClient | None = None,
    tools: ToolExecutor | None = None,
    spec_path: str | Path | None = None,
) -> dict[NodeKind, NodeExecutor]:
    kinds = {n.kind for n in plan.nodes}
    executors: dict[NodeKind, NodeExecutor] = {}
    if (
        kinds & NEEDS_MODEL
        and model is None
        and any(
            n.kind is NodeKind.LLM or not n.config.get("fn")
            for n in plan.nodes
            if n.kind in NEEDS_MODEL
        )
    ):
        model = GatewayModelClient(spec_models=spec.models)
    if NodeKind.LLM in kinds and model is not None:
        executors[NodeKind.LLM] = LlmNodeExecutor(model, functions)
    if NodeKind.ROUTER in kinds:
        executors[NodeKind.ROUTER] = RouterNodeExecutor(
            model, functions, router_factory(spec, model, spec_path)
        )
    if kinds & {NodeKind.TOOL, NodeKind.AGENT} and any(
        n.config.get("tools") or n.kind is NodeKind.TOOL for n in plan.nodes
    ):
        if tools is None:
            raise LookupError(
                f"spec '{spec.name}' has tool nodes: pass a tool executor (the authorising "
                "executor with this spec's grants; see agent_tools.assemble)"
            )
        executors[NodeKind.TOOL] = ToolNodeExecutor(tools, tool_names(plan))
    if NodeKind.AGENT in kinds:
        if model is None:
            raise LookupError(f"spec '{spec.name}' has agent nodes: a model client is needed")
        registry = getattr(tools, "registry", None)
        if tools is None or registry is None:
            if any(n.config.get("tools") for n in plan.nodes if n.kind is NodeKind.AGENT):
                raise LookupError("agent nodes with tools need a tool executor with a registry")
            tools, registry = _no_tools()
        executors[NodeKind.AGENT] = agent_dispatcher(spec, plan, model, tools, registry)
    if NodeKind.SUBPLAN in kinds:
        base = Path(spec_path).parent if spec_path else Path.cwd()

        async def run_subplan(ref: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
            child = load_spec(base / ref)
            child_plan = compile_spec(child)
            child_functions = functions_for(child_plan)
            runner = NodeRunner(
                child_functions,
                executors_for(
                    child,
                    child_plan,
                    child_functions,
                    model=model,
                    tools=tools,
                    spec_path=base / ref,
                ),
            )
            adapter = runtime_for(child, runner=runner)
            handle = await adapter.build(child_plan)
            return await adapter.invoke(handle, inputs, ctx)

        executors[NodeKind.SUBPLAN] = SubplanNodeExecutor(run_subplan)
    return executors


def _create(slot: str, name: str, **kwargs: Any) -> Any:
    """Create a component, passing only the collaborators its constructor accepts (test-double
    components take none)."""
    try:
        return component_registry.create(slot, name, **kwargs)
    except KeyError:
        installed = ", ".join(component_registry.names(slot)) or "none"
        raise LookupError(f"{slot} '{name}' is not installed (installed: {installed})") from None
    except TypeError:
        return component_registry.create(slot, name)


def load_examples(path: Path) -> dict[str, list[str]]:
    """Labelled examples for an embedding router: JSON lines of ``{"text": ..., "route": ...}``."""
    import json

    examples: dict[str, list[str]] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            examples.setdefault(str(row["route"]), []).append(str(row["text"]))
    return examples


def router_factory(
    spec: AgentSpec, model: ModelClient | None, spec_path: str | Path | None = None
) -> Callable[[PlanNode], Router]:
    """Builds (once per node) the router named by ``config.router``, else ``components.router``,
    else ``llm``: ``embedding`` takes ``examples`` or ``examples_file`` (relative to the spec)
    and the ``components.embedder`` (default ``gateway``); ``rules`` takes ``rules`` and
    ``default``; other installed routers are created by name."""
    base = Path(spec_path).parent if spec_path else Path.cwd()
    built: dict[str, Router] = {}
    embedders: dict[str, Embedder] = {}

    def embedder() -> Embedder:
        name = spec.components.get("embedder", "gateway")
        if name not in embedders:
            kwargs = {"client": model} if isinstance(model, GatewayModelClient) else {}
            created = _create("embedder", name, **kwargs)
            if not isinstance(created, Embedder):
                raise LookupError(f"embedder '{name}' is not an Embedder")
            embedders[name] = created
        return embedders[name]

    def make(node: PlanNode) -> Router:
        cfg = dict(node.config.get("config") or {})
        name = str(cfg.get("router") or spec.components.get("router", "llm"))
        kwargs: dict[str, Any] = {}
        if name == "llm":
            if model is None:
                raise LookupError(f"router '{node.id}' uses the LLM router: a model is needed")
            kwargs = {
                "model": model,
                "role": str(node.config.get("model") or "router"),
                "descriptions": cfg.get("descriptions"),
            }
        elif name == "embedding":
            examples = cfg.get("examples") or (
                load_examples(base / str(cfg["examples_file"]))
                if cfg.get("examples_file")
                else None
            )
            if not examples:
                raise LookupError(f"router '{node.id}': the embedding router needs examples")
            kwargs = {"embedder": embedder(), "examples": examples, "k": int(cfg.get("k", 3))}
        elif name == "rules":
            kwargs = {"rules": cfg.get("rules", ()), "default": cfg.get("default")}
        router = _create("router", name, **kwargs)
        if not isinstance(router, Router):
            raise LookupError(f"router '{name}' is not a Router")
        return router

    def cached(node: PlanNode) -> Router:
        if node.id not in built:
            built[node.id] = make(node)
        return built[node.id]

    return cached


def agent_dispatcher(
    spec: AgentSpec,
    plan: ExecutablePlan,
    model: ModelClient,
    tools: ToolExecutor,
    registry: ToolRegistry,
) -> AgentLoop:
    """The executor for agent nodes: the spec's ReAct engine plus the planning and reflection
    strategies (Step 14), with planner and critic from the spec's components."""
    names = tool_names(plan)
    engine = spec.components.get("agent_loop", "native")
    planner_name = spec.components.get("planner", "llm")
    critic_name = spec.components.get("critic", "llm")

    def react(m: ModelClient) -> AgentLoop:
        loop = _create("agent_loop", engine, model=m, tools=tools, registry=registry, names=names)
        assert isinstance(loop, AgentLoop)
        return loop

    def native(m: ModelClient) -> AgentLoop:  # strategies' sub-steps use the native loop
        return AgentLoopExecutor(m, tools, registry, names)

    def planner(m: ModelClient) -> Planner:
        return _create("planner", planner_name, model=m)  # type: ignore[no-any-return]

    def critic(m: ModelClient, role: str) -> Critic:
        return _create("critic", critic_name, model=m, role=role)  # type: ignore[no-any-return]

    common: dict[str, Any] = {"model": model, "tools": tools, "registry": registry, "names": names}
    return AgentNodeDispatcher(
        {
            "react": react(model),
            "plan_execute": PlanExecuteStrategy(**common, planner=planner, react=native),
            "rewoo": RewooStrategy(**common, planner=planner),
            "reflection": ReflectionStrategy(**common, critic=critic, react=native),
            "evaluator_optimiser": EvaluatorOptimiserStrategy(
                **common, critic=critic, react=native
            ),
            "agentic_rag": AgenticRagStrategy(**common),
        }
    )


def _no_tools() -> tuple[ToolExecutor, ToolRegistry]:
    from agent_tools.executor import PlatformToolExecutor
    from agent_tools.registry import InMemoryToolRegistry

    registry = InMemoryToolRegistry()
    return PlatformToolExecutor(registry), registry


def adapter_for(
    spec: AgentSpec,
    plan: ExecutablePlan,
    *,
    model: ModelClient | None = None,
    tools: ToolExecutor | None = None,
    spec_path: str | Path | None = None,
    runner: NodeRunner | None = None,
) -> RuntimeAdapter:
    """The spec's runtime adapter with functions and executors plugged in. Test-double adapters
    (Step 4) take functions only."""
    functions = functions_for(plan)
    if runner is None:
        executors = executors_for(
            spec, plan, functions, model=model, tools=tools, spec_path=spec_path
        )
        if spec.runtime.adapter in ("sequential", "stepwise") and not executors:
            return runtime_for(spec, functions=functions)
        runner = NodeRunner(functions, executors)
    return runtime_for(spec, runner=runner)


async def run(
    spec: AgentSpec,
    inputs: Mapping[str, Any],
    ctx: RunContext,
    provider: TracerProvider | None = None,
    *,
    model: ModelClient | None = None,
    tools: ToolExecutor | None = None,
    spec_path: str | Path | None = None,
    runner: NodeRunner | None = None,
) -> tuple[RunResult, str]:
    """Execute ``spec`` and return the result and the trace id (hex).

    Pass ``runner`` (``NodeRunner(..., replay=journal)``) to replay a recorded run; its
    ``journal(run_id)`` holds what this run did.
    """
    plan = compile_spec(spec)
    adapter = adapter_for(spec, plan, model=model, tools=tools, spec_path=spec_path, runner=runner)
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
        output=next((state.values[e] for e in reversed(plan.exits) if e in state.values), None),
    )
    tracing.flush(provider)
    return result, trace_id


async def run_file(
    path: str | Path, inputs: Mapping[str, Any], ctx: RunContext
) -> tuple[RunResult, str]:
    return await run(load_spec(path), inputs, ctx, spec_path=path)
