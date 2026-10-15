"""``durability: temporal`` (Step 19): the runtime adapter and the ``durable_executor`` on Temporal.

``TemporalRuntime`` implements the platform's runtime adapter contract, so the run API, tests and
tools use a Temporal-backed run exactly like a LangGraph or Agent Framework one: ``invoke`` starts
the ``agent-run`` workflow (workflow id ``agent-run-<run id>``: starting the same run twice is a
no-op) and returns when the run finishes or waits for an approval; ``resume`` signals the
approval; ``cancel`` cancels the workflow; ``get_state`` queries it. ``worker()`` runs the
workflows and the ``run_node`` activity for the plans this process serves.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

from agent_contracts.interfaces import DurableExecutor, RuntimeAdapter
from agent_contracts.models import (
    ExecutablePlan,
    RunContext,
    RunEvent,
    RunEventType,
    RunResult,
    RunState,
    RunStatus,
)
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from agent_runtime.nodes import NodeRunner, max_steps
from agent_runtime.temporal import registry
from agent_runtime.temporal.activities import REF, NodeActivities
from agent_runtime.temporal.workflows import AgentRunWorkflow, FunctionWorkflow

TASK_QUEUE = "agent-platform"
TERMINAL = {"succeeded", "failed", "stopped", "cancelled"}


def sandbox() -> SandboxedWorkflowRunner:
    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules(
            "agent_contracts", "agent_runtime", "pydantic", "pydantic_core"
        )
    )


class TemporalRuntime(RuntimeAdapter):
    component_status = "implemented"

    def __init__(
        self,
        runner: NodeRunner | None = None,
        *,
        client: Client | None = None,
        address: str | None = None,
        namespace: str | None = None,
        task_queue: str = TASK_QUEUE,
        artefacts: Any = None,
        approval_timeout_seconds: float | None = None,
        poll_seconds: float = 0.1,
    ) -> None:
        self.runner = runner or NodeRunner()
        self._client = client
        self.address = address or os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")
        self.namespace = namespace or os.environ.get("TEMPORAL_NAMESPACE", "default")
        self.task_queue = task_queue
        self.activities = NodeActivities(artefacts=artefacts)
        self.approval_timeout_seconds = approval_timeout_seconds
        self.poll_seconds = poll_seconds
        self.plans: dict[str, ExecutablePlan] = {}
        self.contexts: dict[str, RunContext] = {}

    async def client(self) -> Client:
        if self._client is None:
            self._client = await Client.connect(self.address, namespace=self.namespace)
        return self._client

    @contextlib.asynccontextmanager
    async def worker(self) -> AsyncIterator[Worker]:
        """Serve this process's plans (call ``build`` first) until the context exits."""
        w = Worker(
            await self.client(),
            task_queue=self.task_queue,
            workflows=[AgentRunWorkflow, FunctionWorkflow],
            activities=[self.activities.run_node],
            workflow_runner=sandbox(),
        )
        async with w:
            yield w

    async def build(self, plan: ExecutablePlan) -> str:
        handle = f"{plan.name}@{plan.version}"
        self.plans[handle] = plan
        self.activities.serve(handle, plan, self.runner)
        return handle

    def _wf(self, client: Client, run_id: str) -> WorkflowHandle[Any, Any]:
        return client.get_workflow_handle(f"agent-run-{run_id}")

    async def invoke(self, handle: str, inputs: Mapping[str, Any], ctx: RunContext) -> RunResult:
        client = await self.client()
        self.contexts[ctx.run_id] = ctx
        plan = self.plans[handle]
        req = {
            "handle": handle,
            "plan": plan.model_dump(mode="json"),
            "inputs": dict(inputs),
            "ctx": ctx.model_dump(mode="json"),
            "max_steps": max_steps(plan),
            "approval_timeout_seconds": self.approval_timeout_seconds,
        }
        with contextlib.suppress(WorkflowAlreadyStartedError):
            await client.start_workflow(
                AgentRunWorkflow.run,
                req,
                id=f"agent-run-{ctx.run_id}",
                task_queue=self.task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        return await self._settle(ctx.run_id)

    def stream(
        self, handle: str, inputs: Mapping[str, Any], ctx: RunContext
    ) -> AsyncIterator[RunEvent]:
        async def events() -> AsyncIterator[RunEvent]:
            yield RunEvent(type=RunEventType.RUN_STARTED, run_id=ctx.run_id, data={"plan": handle})
            result = await self.invoke(handle, inputs, ctx)
            yield RunEvent(
                type=RunEventType.RUN_FINISHED,
                run_id=ctx.run_id,
                data={"status": result.status.value},
            )

        return events()

    async def resume(
        self, handle: str, run_id: str, payload: Mapping[str, Any], ctx: RunContext
    ) -> RunResult:
        state = await self._query(run_id)
        if state["status"] != "interrupted" or not state["waiting"]:
            raise ValueError(f"run {run_id} is {state['status']}, not interrupted")
        await self._wf(await self.client(), run_id).signal(
            AgentRunWorkflow.approve, args=[state["waiting"][0], dict(payload)]
        )
        for _ in range(100):  # wait until the workflow has taken the decision
            if (await self._query(run_id))["waiting"] != state["waiting"]:
                break
            await asyncio.sleep(self.poll_seconds)
        return await self._settle(run_id)

    async def cancel(self, run_id: str) -> None:
        try:
            await self._wf(await self.client(), run_id).cancel()
        except RPCError as exc:
            raise KeyError(run_id) from exc

    async def get_state(self, run_id: str) -> RunState:
        return self._run_state(run_id, await self._query(run_id))

    async def _query(self, run_id: str) -> dict[str, Any]:
        wf = self._wf(await self.client(), run_id)
        desc = await wf.describe()
        if desc.status == WorkflowExecutionStatus.RUNNING:
            state: dict[str, Any] = await wf.query(AgentRunWorkflow.state)
            if state["status"] not in TERMINAL:
                return state
            # finished in memory, completion not yet recorded: the result carries the output
        if desc.status in (WorkflowExecutionStatus.RUNNING, WorkflowExecutionStatus.COMPLETED):
            result: dict[str, Any] = await wf.result()
            return result
        if desc.status == WorkflowExecutionStatus.CANCELED:
            return {"status": "cancelled", "values": {}, "waiting": [], "error": None}
        name = desc.status.name if desc.status is not None else "UNKNOWN"
        return {"status": "failed", "values": {}, "waiting": [], "error": name}

    async def _settle(self, run_id: str) -> RunResult:
        while True:
            state = await self._query(run_id)
            if state["status"] in TERMINAL or (
                state["status"] == "interrupted" and state["waiting"]
            ):
                break
            await asyncio.sleep(self.poll_seconds)
        rs = self._run_state(run_id, state)
        return RunResult(
            run_id=run_id,
            status=rs.status,
            output=await self._deref(state.get("output")),
            state=rs,
        )

    async def _deref(self, value: Any) -> Any:
        if isinstance(value, Mapping) and set(value) == {REF} and self.activities.artefacts:
            import json

            return json.loads(await self.activities.artefacts.get(value[REF]))
        return value

    def _run_state(self, run_id: str, state: Mapping[str, Any]) -> RunState:
        ctx = self.contexts.get(run_id)
        return RunState(
            run_id=run_id,
            thread_id=ctx.thread_id if ctx else run_id,
            status=RunStatus(state["status"]),
            values=dict(state.get("values") or {}),
            next_nodes=tuple(state.get("waiting") or ()),
            error=state.get("error"),
            stop_reason=state.get("stop_reason"),
        )


class TemporalDurableExecutor(DurableExecutor):
    """``durable_executor.temporal``: registered async functions as Temporal workflows."""

    component_status = "implemented"

    def __init__(
        self,
        workflows: Mapping[str, Callable[[Any], Awaitable[Any]]] | None = None,
        *,
        client: Client | None = None,
        address: str | None = None,
        namespace: str | None = None,
        task_queue: str = TASK_QUEUE,
        id_prefix: str = "",
    ) -> None:
        registry.FUNCTIONS.update(dict(workflows or {}))
        self._client, self.task_queue, self.id_prefix = client, task_queue, id_prefix
        self.address = address or os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")
        self.namespace = namespace or os.environ.get("TEMPORAL_NAMESPACE", "default")

    async def client(self) -> Client:
        if self._client is None:
            self._client = await Client.connect(self.address, namespace=self.namespace)
        return self._client

    @contextlib.asynccontextmanager
    async def worker(self) -> AsyncIterator[Worker]:
        """Run the registered functions until the context exits."""
        w = Worker(
            await self.client(),
            task_queue=self.task_queue,
            workflows=[FunctionWorkflow],
            workflow_runner=sandbox(),
        )
        async with w:
            yield w

    async def start(
        self, workflow: str, inputs: Mapping[str, Any], ctx: RunContext, idempotency_key: str
    ) -> str:
        wid = f"{self.id_prefix}fn-{ctx.tenant_id}-{idempotency_key}"
        with contextlib.suppress(WorkflowAlreadyStartedError):
            await (await self.client()).start_workflow(
                FunctionWorkflow.run,
                args=[workflow, dict(inputs)],
                id=wid,
                task_queue=self.task_queue,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        return wid

    async def signal(self, run_id: str, name: str, payload: Mapping[str, Any]) -> None:
        await (
            (await self.client())
            .get_workflow_handle(run_id)
            .signal(FunctionWorkflow.deliver, args=[name, dict(payload)])
        )

    async def status(self, run_id: str) -> RunState:
        desc = await (await self.client()).get_workflow_handle(run_id).describe()
        statuses = {
            WorkflowExecutionStatus.RUNNING: RunStatus.RUNNING,
            WorkflowExecutionStatus.COMPLETED: RunStatus.SUCCEEDED,
            WorkflowExecutionStatus.CANCELED: RunStatus.CANCELLED,
        }
        status = statuses.get(desc.status, RunStatus.FAILED) if desc.status else RunStatus.FAILED
        return RunState(run_id=run_id, thread_id=run_id, status=status)

    async def result(self, run_id: str, wait_seconds: float = 30.0) -> RunResult:
        handle = (await self.client()).get_workflow_handle(run_id)
        output = await asyncio.wait_for(handle.result(), wait_seconds)
        state = RunState(run_id=run_id, thread_id=run_id, status=RunStatus.SUCCEEDED)
        return RunResult(run_id=run_id, status=RunStatus.SUCCEEDED, output=output, state=state)
