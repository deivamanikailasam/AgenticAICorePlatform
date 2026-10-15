from __future__ import annotations

import asyncio
import inspect
import itertools
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

from agent_contracts.dummies._util import mark_dummies
from agent_contracts.interfaces import PatternBuilder, RuntimeAdapter
from agent_contracts.models import (
    ExecutablePlan,
    NodeKind,
    PlanEdge,
    PlanFragment,
    PlanNode,
    RunContext,
    RunEvent,
    RunEventType,
    RunResult,
    RunState,
    RunStatus,
)

NodeFn = Callable[[dict[str, Any]], Any | Awaitable[Any]]


def _hello(state: dict[str, Any]) -> dict[str, Any]:
    return {"greeting": f"hello, {state.get('input', {}).get('name', 'world')}"}


BUILTIN_FUNCTIONS: dict[str, NodeFn] = {
    "identity": lambda state: state.get("input"),
    "hello": _hello,
}


class SingleNodePattern(PatternBuilder):
    """One function node: ``{fn: name}``."""

    def build(self, section: Mapping[str, Any], prefix: str) -> PlanFragment:
        node = PlanNode(id=f"{prefix}main", kind=NodeKind.FUNCTION, config={"fn": section["fn"]})
        return PlanFragment(nodes=(node,), entry=node.id, exits=(node.id,))


class ChainPattern(PatternBuilder):
    """A straight chain of function nodes: ``{steps: [fn, fn, ...]}``."""

    def build(self, section: Mapping[str, Any], prefix: str) -> PlanFragment:
        steps = list(section["steps"])
        if not steps:
            raise ValueError("chain needs at least one step")
        nodes = tuple(
            PlanNode(id=f"{prefix}{i}", kind=NodeKind.FUNCTION, config={"fn": fn})
            for i, fn in enumerate(steps)
        )
        edges = tuple(PlanEdge(source=a.id, target=b.id) for a, b in itertools.pairwise(nodes))
        return PlanFragment(nodes=nodes, edges=edges, entry=nodes[0].id, exits=(nodes[-1].id,))


class _Run:
    def __init__(self, plan: ExecutablePlan, ctx: RunContext, inputs: Mapping[str, Any]) -> None:
        self.plan = plan
        self.ctx = ctx
        self.values: dict[str, Any] = {"input": dict(inputs)}
        self.status = RunStatus.RUNNING
        self.queue: deque[str] = deque([plan.entry])
        self.error: str | None = None
        self.output: Any = None
        self.cancelled = False

    def state(self) -> RunState:
        return RunState(
            run_id=self.ctx.run_id,
            thread_id=self.ctx.thread_id,
            status=self.status,
            values=dict(self.values),
            next_nodes=tuple(self.queue),
            error=self.error,
        )


class _GraphWalker(RuntimeAdapter):
    """Shared engine for the two dummy adapters: walks a plan, function nodes call ``functions``.

    Approval nodes interrupt the run until ``resume`` is called with ``{"approved": bool}``.
    Edge conditions match a node's outcome: ``"ok"`` by default, ``"error"`` if it raised,
    or the ``"_outcome"`` key a function returns.
    """

    def __init__(self, functions: Mapping[str, NodeFn] | None = None) -> None:
        self._functions = {**BUILTIN_FUNCTIONS, **(functions or {})}
        self._plans: dict[str, ExecutablePlan] = {}
        self._runs: dict[str, _Run] = {}

    async def build(self, plan: ExecutablePlan) -> str:
        handle = f"{plan.name}@{plan.version}"
        self._plans[handle] = plan
        return handle

    def _next_batch(self, run: _Run) -> list[str]:
        return [run.queue.popleft()]

    async def _exec_node(self, run: _Run, node: PlanNode) -> str:
        if node.kind is NodeKind.APPROVAL and node.id not in run.values:
            run.status = RunStatus.INTERRUPTED
            run.queue.appendleft(node.id)
            return "interrupt"
        if node.kind is NodeKind.FUNCTION:
            fn = self._functions[node.config["fn"]]
            try:
                result = fn(run.values)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                run.values[node.id] = {"error": str(exc)}
                return "error"
            run.values[node.id] = result
            run.output = result
            if isinstance(result, dict) and "_outcome" in result:
                return str(result["_outcome"])
            return "ok"
        if node.kind is NodeKind.APPROVAL:
            return "ok" if run.values[node.id].get("approved") else "rejected"
        run.values[node.id] = None
        return "ok"

    async def _drive(self, run: _Run) -> AsyncIterator[RunEvent]:
        rid = run.ctx.run_id
        while run.queue and run.status is RunStatus.RUNNING:
            if run.cancelled:
                run.status = RunStatus.CANCELLED
                break
            for node_id in self._next_batch(run):
                node = run.plan.node(node_id)
                yield RunEvent(type=RunEventType.NODE_STARTED, run_id=rid, node_id=node_id)
                outcome = await self._exec_node(run, node)
                if outcome == "interrupt":
                    yield RunEvent(
                        type=RunEventType.APPROVAL_REQUESTED,
                        run_id=rid,
                        node_id=node_id,
                        data=dict(node.config),
                    )
                    return
                yield RunEvent(
                    type=RunEventType.NODE_FINISHED,
                    run_id=rid,
                    node_id=node_id,
                    data={"outcome": outcome},
                )
                matched = [
                    e.target
                    for e in run.plan.edges
                    if e.source == node_id and e.condition in (None, outcome, f"on_{outcome}")
                ]
                if outcome == "error" and not matched:
                    run.status = RunStatus.FAILED
                    run.error = str(run.values[node_id]["error"])
                    yield RunEvent(
                        type=RunEventType.RUN_ERROR, run_id=rid, data={"error": run.error}
                    )
                    return
                for target in matched:
                    if target not in run.queue:
                        run.queue.append(target)
        if run.status is RunStatus.RUNNING:
            run.status = RunStatus.SUCCEEDED
        yield RunEvent(
            type=RunEventType.RUN_FINISHED, run_id=rid, data={"status": run.status.value}
        )

    def _result(self, run: _Run) -> RunResult:
        return RunResult(
            run_id=run.ctx.run_id, status=run.status, output=run.output, state=run.state()
        )

    async def invoke(self, handle: str, inputs: Mapping[str, Any], ctx: RunContext) -> RunResult:
        async for _ in self.stream(handle, inputs, ctx):
            pass
        return self._result(self._runs[ctx.run_id])

    async def stream(
        self, handle: str, inputs: Mapping[str, Any], ctx: RunContext
    ) -> AsyncIterator[RunEvent]:
        run = _Run(self._plans[handle], ctx, inputs)
        self._runs[ctx.run_id] = run
        yield RunEvent(type=RunEventType.RUN_STARTED, run_id=ctx.run_id, data={"plan": handle})
        async for event in self._drive(run):
            yield event
            await asyncio.sleep(0)

    async def resume(
        self, handle: str, run_id: str, payload: Mapping[str, Any], ctx: RunContext
    ) -> RunResult:
        run = self._runs[run_id]
        if run.status is not RunStatus.INTERRUPTED:
            raise ValueError(f"run {run_id} is {run.status.value}, not interrupted")
        run.values[run.queue[0]] = dict(payload)
        run.status = RunStatus.RUNNING
        async for _ in self._drive(run):
            pass
        return self._result(run)

    async def cancel(self, run_id: str) -> None:
        run = self._runs[run_id]
        run.cancelled = True
        if run.status in (RunStatus.INTERRUPTED, RunStatus.PENDING):
            run.status = RunStatus.CANCELLED

    async def get_state(self, run_id: str) -> RunState:
        return self._runs[run_id].state()


class SequentialRuntimeAdapter(_GraphWalker):
    """Runs one node at a time in queue order. The Step 4 stub adapter."""


class StepwiseRuntimeAdapter(_GraphWalker):
    """Runs every ready node in a 'superstep' before moving on (breadth-first)."""

    def _next_batch(self, run: _Run) -> list[str]:
        batch = list(run.queue)
        run.queue.clear()
        return batch


mark_dummies(globals())
