"""``runtime_adapter.langgraph``: run a compiled plan as a LangGraph ``StateGraph`` (Step 11, default).

Mapping:

* each plan node becomes a graph node; its body is the shared ``NodeRunner`` (``nodes.py``), so
  behaviour matches every other adapter;
* typed state with reducers: ``values`` and ``outcomes`` merge per node id and ``order`` appends,
  so parallel branches never overwrite each other;
* edges become conditional edges that pick targets by outcome; several matching targets run in
  parallel in the same superstep; JOIN nodes are *deferred* until every branch has finished;
* APPROVAL nodes call ``interrupt()``; ``resume`` continues with ``Command(resume=payload)``;
* the recursion limit is the spec's ``budgets.max_steps``: hitting it stops the run (``stopped``)
  with the partial state kept;
* streaming uses LangGraph's ``debug`` and ``updates`` modes, translated into ``RunEvent``s;
* each run executes in its own task, so ``cancel`` stops it even in the middle of a node.

State is checkpointed per run (``thread_id = run_id``) in the given LangGraph checkpointer:
in memory by default; Postgres in Step 18.
"""

from __future__ import annotations

import asyncio
import contextlib
import operator
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from agent_contracts.interfaces import RuntimeAdapter
from agent_contracts.models import (
    ExecutablePlan,
    NodeKind,
    PlanNode,
    RunContext,
    RunEvent,
    RunEventType,
    RunResult,
    RunState,
    RunStatus,
)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent_runtime.nodes import (
    NodeExecutor,
    NodeFn,
    NodeOutcome,
    NodeRunner,
    approval_outcome,
    compensate,
    max_steps,
    next_targets,
    run_output,
)


def _merge(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    return {**(left or {}), **(right or {})}


class GraphState(TypedDict, total=False):
    input: dict[str, Any]
    values: Annotated[dict[str, Any], _merge]
    outcomes: Annotated[dict[str, str], _merge]
    order: Annotated[list[str], operator.add]
    failed: Annotated[dict[str, str], _merge]  # node id -> error, when no edge handles it
    stopped: Annotated[
        dict[str, str], _merge
    ]  # node id -> reason (a limit), when no edge handles it


@dataclass
class _Run:
    handle: str
    ctx: RunContext
    status: RunStatus = RunStatus.PENDING
    error: str | None = None
    stop_reason: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    next_nodes: tuple[str, ...] = ()
    task: asyncio.Task[None] | None = None


_DONE = object()


class LangGraphAdapter(RuntimeAdapter):
    def __init__(
        self,
        functions: Mapping[str, NodeFn] | None = None,
        executors: Mapping[NodeKind, NodeExecutor] | None = None,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        runner: NodeRunner | None = None,
    ) -> None:
        self.runner = runner or NodeRunner(functions, executors)
        self.checkpointer = checkpointer or InMemorySaver()
        self._plans: dict[str, ExecutablePlan] = {}
        self._graphs: dict[str, Any] = {}
        self._runs: dict[str, _Run] = {}

    # ------------------------------------------------------------------ build

    def _node_fn(self, plan: ExecutablePlan, node: PlanNode) -> Any:
        runner = self.runner
        has_error_route = bool(next_targets(plan, node.id, "error"))
        has_stop_route = bool(next_targets(plan, node.id, "stopped"))

        async def body(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
            ctx = config["configurable"]["agent_ctx"]
            if node.kind is NodeKind.APPROVAL and runner.replay is None:
                payload = interrupt({"node": node.id, **node.config})
                result: NodeOutcome = runner.record(ctx, node.id, approval_outcome(payload))
            else:
                values = {"input": state.get("input", {}), **state.get("values", {})}
                result = await runner.run(node, values, ctx)
            update: dict[str, Any] = {
                "values": {node.id: result.value},
                "outcomes": {node.id: result.outcome},
                "order": [node.id],
            }
            if result.outcome == "error" and not has_error_route:
                update["failed"] = {node.id: result.error or "failed"}
            if result.outcome == "stopped" and not has_stop_route:
                update["stopped"] = {node.id: result.error or "stopped"}
            return update

        return body

    def _router(self, plan: ExecutablePlan, node_id: str) -> Any:
        def route(state: GraphState) -> list[str] | str:
            if state.get("failed") or state.get("stopped"):
                return END
            targets = next_targets(plan, node_id, state.get("outcomes", {}).get(node_id, "ok"))
            return targets or END

        return route

    async def build(self, plan: ExecutablePlan) -> str:
        graph = StateGraph(GraphState)
        for node in plan.nodes:
            graph.add_node(node.id, self._node_fn(plan, node), defer=node.kind is NodeKind.JOIN)
        graph.add_edge(START, plan.entry)
        for node in plan.nodes:
            destinations = sorted({e.target for e in plan.edges if e.source == node.id})
            graph.add_conditional_edges(node.id, self._router(plan, node.id), [*destinations, END])
        handle = f"{plan.name}@{plan.version}"
        self._plans[handle] = plan
        self._graphs[handle] = graph.compile(checkpointer=self.checkpointer, name=plan.name)
        return handle

    # ------------------------------------------------------------------ run

    def _config(self, run: _Run) -> dict[str, Any]:
        plan = self._plans[run.handle]
        return {
            "configurable": {"thread_id": run.ctx.run_id, "agent_ctx": run.ctx},
            "recursion_limit": max_steps(plan),
        }

    async def _execute(
        self, run: _Run, graph_input: Any, queue: asyncio.Queue[RunEvent | object]
    ) -> None:
        rid = run.ctx.run_id
        graph = self._graphs[run.handle]
        interrupted = False
        try:
            async for mode, chunk in graph.astream(
                graph_input, self._config(run), stream_mode=["debug", "updates"]
            ):
                if mode == "debug" and chunk["type"] == "task":
                    await queue.put(
                        RunEvent(
                            type=RunEventType.NODE_STARTED,
                            run_id=rid,
                            node_id=chunk["payload"]["name"],
                        )
                    )
                elif mode == "updates" and "__interrupt__" in chunk:
                    interrupted = True
                    for item in chunk["__interrupt__"]:
                        data = dict(item.value) if isinstance(item.value, Mapping) else {}
                        await queue.put(
                            RunEvent(
                                type=RunEventType.APPROVAL_REQUESTED,
                                run_id=rid,
                                node_id=str(data.pop("node", "")) or None,
                                data=data,
                            )
                        )
                elif mode == "updates":
                    for node_id, update in chunk.items():
                        outcome = (update or {}).get("outcomes", {}).get(node_id)
                        await queue.put(
                            RunEvent(
                                type=RunEventType.NODE_FINISHED,
                                run_id=rid,
                                node_id=node_id,
                                data={"outcome": outcome},
                            )
                        )
            await self._settle(run, interrupted)
            if run.status is RunStatus.FAILED:
                await self._compensate(run, queue)
        except GraphRecursionError:
            await self._settle(run, False)
            run.status = RunStatus.STOPPED
            run.stop_reason = f"max_steps ({max_steps(self._plans[run.handle])}) reached"
        except asyncio.CancelledError:
            await self._settle(run, False)
            run.status = RunStatus.CANCELLED
            raise
        except Exception as exc:  # an engine failure, not a node failure
            run.status = RunStatus.FAILED
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            if run.status is RunStatus.FAILED:
                await queue.put(
                    RunEvent(type=RunEventType.RUN_ERROR, run_id=rid, data={"error": run.error})
                )
            if run.status is not RunStatus.INTERRUPTED:
                await queue.put(
                    RunEvent(
                        type=RunEventType.RUN_FINISHED,
                        run_id=rid,
                        data={"status": run.status.value},
                    )
                )
            await queue.put(_DONE)

    async def _compensate(self, run: _Run, queue: asyncio.Queue[RunEvent | object]) -> None:
        plan = self._plans[run.handle]
        values = {"input": run.state.get("input", {}), **run.state.get("values", {})}
        undone = await compensate(
            plan,
            self.runner,
            values,
            run.state.get("order", []),
            run.state.get("outcomes", {}),
            run.ctx,
        )
        for comp_id, result in undone:
            run.state.setdefault("values", {})[comp_id] = result.value
            await queue.put(
                RunEvent(
                    type=RunEventType.NODE_FINISHED,
                    run_id=run.ctx.run_id,
                    node_id=comp_id,
                    data={"outcome": result.outcome, "compensation": True},
                )
            )
        if undone:
            run.error = f"{run.error}; compensated: {', '.join(c for c, _ in undone)}"

    async def _settle(self, run: _Run, interrupted: bool) -> None:
        snapshot = await self._graphs[run.handle].aget_state(self._config(run))
        run.state = dict(snapshot.values or {})
        run.next_nodes = tuple(snapshot.next or ())
        failed = run.state.get("failed") or {}
        if interrupted:
            run.status = RunStatus.INTERRUPTED
        elif failed:
            run.status = RunStatus.FAILED
            run.error = "; ".join(f"{k}: {v}" for k, v in failed.items())
        elif run.state.get("stopped"):
            run.status = RunStatus.STOPPED
            run.stop_reason = "; ".join(f"{k}: {v}" for k, v in run.state["stopped"].items())
        else:
            run.status = RunStatus.SUCCEEDED

    async def _drive(self, run: _Run, graph_input: Any) -> AsyncIterator[RunEvent]:
        queue: asyncio.Queue[RunEvent | object] = asyncio.Queue()
        run.status = RunStatus.RUNNING
        run.task = asyncio.create_task(self._execute(run, graph_input, queue))
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                assert isinstance(item, RunEvent)
                yield item
        finally:
            if run.task is not None and not run.task.done():
                run.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run.task

    def _result(self, run: _Run) -> RunResult:
        plan = self._plans[run.handle]
        values = run.state.get("values", {})
        return RunResult(
            run_id=run.ctx.run_id,
            status=run.status,
            output=run_output(plan, values, run.state.get("order", [])),
            state=self._run_state(run),
        )

    def _run_state(self, run: _Run) -> RunState:
        return RunState(
            run_id=run.ctx.run_id,
            thread_id=run.ctx.thread_id,
            status=run.status,
            values={"input": run.state.get("input", {}), **run.state.get("values", {})},
            next_nodes=run.next_nodes,
            error=run.error,
            stop_reason=run.stop_reason,
        )

    async def stream(
        self, handle: str, inputs: Mapping[str, Any], ctx: RunContext
    ) -> AsyncIterator[RunEvent]:
        run = _Run(handle=handle, ctx=ctx)
        self._plans[handle]  # unknown handle -> KeyError before anything starts
        self._runs[ctx.run_id] = run
        yield RunEvent(type=RunEventType.RUN_STARTED, run_id=ctx.run_id, data={"plan": handle})
        start: GraphState = {"input": dict(inputs), "values": {}, "outcomes": {}, "order": []}
        async for event in self._drive(run, start):
            yield event

    async def invoke(self, handle: str, inputs: Mapping[str, Any], ctx: RunContext) -> RunResult:
        async for _ in self.stream(handle, inputs, ctx):
            pass
        return self._result(self._runs[ctx.run_id])

    async def resume(
        self, handle: str, run_id: str, payload: Mapping[str, Any], ctx: RunContext
    ) -> RunResult:
        run = self._runs[run_id]
        if run.status is not RunStatus.INTERRUPTED:
            raise ValueError(f"run {run_id} is {run.status.value}, not interrupted")
        async for _ in self._drive(run, Command(resume=dict(payload))):
            pass
        return self._result(run)

    async def cancel(self, run_id: str) -> None:
        run = self._runs[run_id]
        if run.task is not None and not run.task.done():
            run.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run.task
        if run.status in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.INTERRUPTED):
            run.status = RunStatus.CANCELLED

    async def get_state(self, run_id: str) -> RunState:
        return self._run_state(self._runs[run_id])
