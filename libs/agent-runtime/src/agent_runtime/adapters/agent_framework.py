"""``runtime_adapter.agent_framework``: run a compiled plan as a Microsoft Agent Framework workflow
(Step 11, the second adapter that proves the seam).

Mapping:

* each plan node becomes an ``Executor``; its body is the shared ``NodeRunner`` (``nodes.py``),
  so behaviour matches the LangGraph adapter;
* nodes pass a small ``Step(source, outcome)`` message; each plan edge becomes an edge whose
  condition matches the outcome, so several matching edges fan out in parallel in one superstep;
* edges into a JOIN node become one fan-in edge group: the join runs once every branch has
  delivered (all edges into a JOIN must be unconditional on this engine; ``build`` checks it);
* APPROVAL nodes call ``ctx.request_info``; ``resume`` answers with ``run(responses=...)``;
* ``budgets.max_steps`` is the workflow's ``max_iterations`` (supersteps); exceeding it stops the
  run (``stopped``);
* every run builds its own workflow instance (they are single-run objects) and executes in its
  own task, so ``cancel`` stops it mid-node.

Durability is in memory for now; Step 18 adds checkpoint storage to both adapters.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

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
from agent_framework import (
    Executor,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    response_handler,
)

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

START = "__start__"


@dataclass(frozen=True)
class Step:
    source: str
    outcome: str


@dataclass
class _Run:
    handle: str
    ctx: RunContext
    workflow: Workflow | None = None
    status: RunStatus = RunStatus.PENDING
    inputs: dict[str, Any] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    outcomes: dict[str, str] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    pending: dict[str, str] = field(default_factory=dict)  # request id -> approval node id
    error: str | None = None
    stop_reason: str | None = None
    task: asyncio.Task[None] | None = None


class _PlanExecutor(Executor):
    """One plan node: run it, record the result, pass the outcome on."""

    def __init__(self, node: PlanNode, plan: ExecutablePlan, run: _Run, runner: NodeRunner):
        super().__init__(id=node.id)
        self.node = node
        self.plan = plan
        self.run = run
        self.runner = runner

    async def _finish(self, result: NodeOutcome, ctx: WorkflowContext[Step]) -> None:
        run, node_id = self.run, self.node.id
        run.values[node_id] = result.value
        run.outcomes[node_id] = result.outcome
        run.order.append(node_id)
        if result.outcome == "error" and not next_targets(self.plan, node_id, "error"):
            run.failed[node_id] = result.error or "failed"
            return
        await ctx.send_message(Step(node_id, result.outcome))

    async def _execute(self, ctx: WorkflowContext[Step]) -> None:
        if self.run.failed:
            return  # another branch already failed the run
        values = {"input": self.run.inputs, **self.run.values}
        await self._finish(await self.runner.run(self.node, values, self.run.ctx), ctx)

    @handler
    async def on_step(self, message: Step, ctx: WorkflowContext[Step]) -> None:
        await self._execute(ctx)

    @handler
    async def on_join(self, messages: list[Step], ctx: WorkflowContext[Step]) -> None:
        await self._execute(ctx)


class _ApprovalExecutor(_PlanExecutor):
    @handler
    async def on_step(self, message: Step, ctx: WorkflowContext[Step]) -> None:
        if self.runner.replay is not None:  # a replay answers from the journal, never asks again
            await self._execute(ctx)
            return
        await ctx.request_info({"node": self.node.id, **self.node.config}, dict)

    @response_handler
    async def on_response(
        self, original: dict[str, Any], response: dict[str, Any], ctx: WorkflowContext[Step]
    ) -> None:
        result = self.runner.record(self.run.ctx, self.node.id, approval_outcome(response))
        await self._finish(result, ctx)


def _condition(condition: str | None) -> Any:
    if condition is None:
        return None
    wanted = {condition, condition.removeprefix("on_")}
    return lambda message: isinstance(message, Step) and message.outcome in wanted


class AgentFrameworkAdapter(RuntimeAdapter):
    def __init__(
        self,
        functions: Mapping[str, NodeFn] | None = None,
        executors: Mapping[NodeKind, NodeExecutor] | None = None,
        runner: NodeRunner | None = None,
    ) -> None:
        self.runner = runner or NodeRunner(functions, executors)
        self._plans: dict[str, ExecutablePlan] = {}
        self._runs: dict[str, _Run] = {}

    # ------------------------------------------------------------------ build

    async def build(self, plan: ExecutablePlan) -> str:
        joins = {n.id for n in plan.nodes if n.kind is NodeKind.JOIN}
        for edge in plan.edges:
            if edge.target in joins and edge.condition is not None:
                raise ValueError(
                    f"edge {edge.source} -> {edge.target}: edges into a join must be "
                    "unconditional on the agent_framework adapter"
                )
        handle = f"{plan.name}@{plan.version}"
        self._plans[handle] = plan
        return handle

    def _workflow(self, plan: ExecutablePlan, run: _Run) -> Workflow:
        executors: dict[str, _PlanExecutor] = {}
        for node in plan.nodes:
            cls = _ApprovalExecutor if node.kind is NodeKind.APPROVAL else _PlanExecutor
            executors[node.id] = cls(node, plan, run, self.runner)
        builder = WorkflowBuilder(
            max_iterations=max_steps(plan), name=plan.name, start_executor=executors[plan.entry]
        )
        joins = {n.id for n in plan.nodes if n.kind is NodeKind.JOIN}
        for join in joins:
            sources = [executors[e.source] for e in plan.edges if e.target == join]
            if sources:
                builder.add_fan_in_edges(sources, executors[join])
        for edge in plan.edges:
            if edge.target in joins:
                continue
            builder.add_edge(
                executors[edge.source],
                executors[edge.target],
                condition=_condition(edge.condition),
            )
        return builder.build()

    # ------------------------------------------------------------------ run

    async def _execute(
        self, run: _Run, kwargs: dict[str, Any], queue: asyncio.Queue[RunEvent | None]
    ) -> None:
        rid = run.ctx.run_id
        assert run.workflow is not None
        interrupted = False
        try:
            async for event in run.workflow.run(stream=True, **kwargs):
                node_id = getattr(event, "executor_id", None)
                if event.type == "executor_invoked" and node_id:
                    await queue.put(
                        RunEvent(type=RunEventType.NODE_STARTED, run_id=rid, node_id=node_id)
                    )
                elif event.type == "executor_completed" and node_id in run.outcomes:
                    await queue.put(
                        RunEvent(
                            type=RunEventType.NODE_FINISHED,
                            run_id=rid,
                            node_id=node_id,
                            data={"outcome": run.outcomes[node_id]},
                        )
                    )
                elif event.type == "request_info":
                    interrupted = True
                    data = dict(event.data) if isinstance(event.data, Mapping) else {}
                    node = str(data.pop("node", ""))
                    run.pending[str(event.request_id)] = node
                    await queue.put(
                        RunEvent(
                            type=RunEventType.APPROVAL_REQUESTED,
                            run_id=rid,
                            node_id=node or None,
                            data=data,
                        )
                    )
            if interrupted:
                run.status = RunStatus.INTERRUPTED
            elif run.failed:
                run.status = RunStatus.FAILED
                run.error = "; ".join(f"{k}: {v}" for k, v in run.failed.items())
                await self._compensate(run, queue)
            else:
                run.status = RunStatus.SUCCEEDED
        except asyncio.CancelledError:
            run.status = RunStatus.CANCELLED
            raise
        except Exception as exc:
            if type(exc).__name__ == "WorkflowConvergenceException":
                run.status = RunStatus.STOPPED
                run.stop_reason = f"max_steps ({max_steps(self._plans[run.handle])}) reached"
            else:
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
            await queue.put(None)

    async def _compensate(self, run: _Run, queue: asyncio.Queue[RunEvent | None]) -> None:
        plan = self._plans[run.handle]
        values = {"input": run.inputs, **run.values}
        undone = await compensate(plan, self.runner, values, run.order, run.outcomes, run.ctx)
        for comp_id, result in undone:
            run.values[comp_id] = result.value
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

    async def _drive(self, run: _Run, kwargs: dict[str, Any]) -> AsyncIterator[RunEvent]:
        queue: asyncio.Queue[RunEvent | None] = asyncio.Queue()
        run.status = RunStatus.RUNNING
        run.task = asyncio.create_task(self._execute(run, kwargs, queue))
        try:
            while (event := await queue.get()) is not None:
                yield event
        finally:
            if not run.task.done():
                run.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run.task

    def _run_state(self, run: _Run) -> RunState:
        return RunState(
            run_id=run.ctx.run_id,
            thread_id=run.ctx.thread_id,
            status=run.status,
            values={"input": run.inputs, **run.values},
            next_nodes=tuple(run.pending.values()) if run.status is RunStatus.INTERRUPTED else (),
            error=run.error,
            stop_reason=run.stop_reason,
        )

    def _result(self, run: _Run) -> RunResult:
        plan = self._plans[run.handle]
        return RunResult(
            run_id=run.ctx.run_id,
            status=run.status,
            output=run_output(plan, run.values, run.order),
            state=self._run_state(run),
        )

    async def stream(
        self, handle: str, inputs: Mapping[str, Any], ctx: RunContext
    ) -> AsyncIterator[RunEvent]:
        plan = self._plans[handle]
        run = _Run(handle=handle, ctx=ctx, inputs=dict(inputs))
        run.workflow = self._workflow(plan, run)
        self._runs[ctx.run_id] = run
        yield RunEvent(type=RunEventType.RUN_STARTED, run_id=ctx.run_id, data={"plan": handle})
        async for event in self._drive(run, {"message": Step(START, "ok")}):
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
        responses = {request_id: dict(payload) for request_id in run.pending}
        run.pending.clear()
        async for _ in self._drive(run, {"responses": responses}):
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
