"""Temporal workflows (Step 19). Workflow code must be deterministic: it only decides what runs
next; every node (model, tool, function, agent) runs in an activity.

* ``AgentRunWorkflow`` interprets a compiled plan: each node is a ``run_node`` activity with a
  retry policy and heartbeats; APPROVAL nodes wait for an ``approve`` signal behind a durable
  timer (default 7 days), so a run can wait for days and survive worker and server restarts;
  failed runs are compensated newest first (saga, Step 12); routing uses the platform's shared
  ``next_targets`` so the plan behaves as on the other engines.
* ``FunctionWorkflow`` runs a registered async function with a handle (inputs, signals) for the
  ``durable_executor`` slot's contract.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from agent_contracts.models import ExecutablePlan, NodeKind

    from agent_runtime.nodes import next_targets

APPROVAL_TIMEOUT = timedelta(days=7)
NODE_TIMEOUT = timedelta(minutes=10)
HEARTBEAT_TIMEOUT = timedelta(seconds=30)
RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)


@workflow.defn(name="agent-run")
class AgentRunWorkflow:
    def __init__(self) -> None:
        self.status = "running"
        self.values: dict[str, Any] = {}
        self.outcomes: dict[str, str] = {}
        self.order: list[str] = []
        self.waiting: list[str] = []
        self.decisions: dict[str, dict[str, Any]] = {}
        self.error: str | None = None
        self.stop_reason: str | None = None

    @workflow.signal
    def approve(self, node_id: str, decision: dict[str, Any]) -> None:
        self.decisions[node_id] = dict(decision)

    @workflow.query
    def state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "values": self.values,
            "outcomes": self.outcomes,
            "order": self.order,
            "waiting": list(self.waiting),
            "error": self.error,
            "stop_reason": self.stop_reason,
        }

    async def _approval(self, node_id: str, wait: timedelta) -> tuple[str, Any]:
        self.waiting.append(node_id)
        self.status = "interrupted"
        try:
            await workflow.wait_condition(lambda: node_id in self.decisions, timeout=wait)
        except TimeoutError:
            self.waiting.remove(node_id)
            self.status = "running"
            return "expired", {"error": f"no decision within {wait}"}
        self.waiting.remove(node_id)
        self.status = "running"
        decision = self.decisions.pop(node_id)
        return ("ok" if decision.get("approved") else "rejected"), decision

    async def _step(self, req: dict[str, Any], node: Any, wait: timedelta) -> Any:
        if node.kind is NodeKind.APPROVAL:
            return await self._approval(node.id, wait)
        if node.kind is NodeKind.JOIN:  # all other branches have settled: pass through
            return ("ok", None)
        return await self._node(req, node.id)

    async def _node(self, req: dict[str, Any], node_id: str) -> dict[str, Any]:
        result: dict[str, Any] = await workflow.execute_activity(
            "run_node",
            {**req, "node_id": node_id, "values": {"input": req["inputs"], **self.values}},
            start_to_close_timeout=NODE_TIMEOUT,
            heartbeat_timeout=HEARTBEAT_TIMEOUT,
            retry_policy=RETRY,
        )
        return result

    @workflow.run
    async def run(self, req: dict[str, Any]) -> dict[str, Any]:
        plan = ExecutablePlan.model_validate(req["plan"])
        timeout = timedelta(
            seconds=float(req.get("approval_timeout_seconds") or APPROVAL_TIMEOUT.total_seconds())
        )
        limit = int(req.get("max_steps") or 100)
        ready: list[str] = [plan.entry]
        joins: dict[str, None] = {}
        steps = 0
        while ready or joins:
            if not ready:  # every other branch has settled: release the joins
                ready, joins = list(joins), {}
            batch, ready = ready, []
            todo = [plan.node(n) for n in batch]
            steps += len(todo)
            if steps > limit:
                self.status, self.stop_reason = "stopped", f"max_steps ({limit}) reached"
                break
            results = await asyncio.gather(*(self._step(req, n, timeout) for n in todo))
            failed = False
            for node, result in zip(todo, results, strict=True):
                if isinstance(result, tuple):
                    outcome, value, error = result[0], result[1], None
                else:
                    outcome, value, error = result["outcome"], result["value"], result.get("error")
                self.values[node.id] = value
                self.outcomes[node.id] = outcome
                self.order.append(node.id)
                targets = next_targets(plan, node.id, outcome)
                if outcome == "stopped" and not targets:
                    self.status, self.stop_reason = "stopped", error or "stopped"
                    return self._finish(plan)
                if outcome in ("error", "expired") and not targets:
                    self.error = f"{node.id}: {error or outcome}"
                    failed = True
                for t in targets:
                    if plan.node(t).kind is NodeKind.JOIN:
                        joins[t] = None
                    elif t not in ready:
                        ready.append(t)
            if failed:
                await self._compensate(req, plan)
                self.status = "failed"
                return self._finish(plan)
        if self.status == "running":
            self.status = "succeeded"
        return self._finish(plan)

    async def _compensate(self, req: dict[str, Any], plan: ExecutablePlan) -> None:
        for node_id in reversed(list(self.order)):
            target = plan.node(node_id).config.get("compensation")
            if not target or self.outcomes.get(node_id) == "error":
                continue
            result = await workflow.execute_activity(
                "run_node",
                {
                    **req,
                    "node_id": str(target),
                    "values": {"input": req["inputs"], **self.values, "compensating": node_id},
                },
                start_to_close_timeout=NODE_TIMEOUT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=RETRY,
            )
            self.values[str(target)] = result["value"]

    def _finish(self, plan: ExecutablePlan) -> dict[str, Any]:
        finished = [n for n in self.order if n in self.values]
        exits = [n for n in finished if n in plan.exits]
        pick = exits or finished
        return {**self.state(), "output": self.values[pick[-1]] if pick else None}


@workflow.defn(name="platform-function")
class FunctionWorkflow:
    """For the ``durable_executor`` slot: runs a function registered with the worker."""

    def __init__(self) -> None:
        self.inbox: dict[str, list[Any]] = {}

    @workflow.signal
    def deliver(self, name: str, payload: Any) -> None:
        self.inbox.setdefault(name, []).append(payload)

    @workflow.run
    async def run(self, name: str, inputs: dict[str, Any]) -> Any:
        from agent_runtime.temporal import registry

        fn = registry.FUNCTIONS.get(name)
        if fn is None:
            raise ApplicationError(f"no durable function '{name}'", non_retryable=True)
        return await fn(_Handle(self, inputs))


class _Handle:
    def __init__(self, wf: FunctionWorkflow, inputs: dict[str, Any]) -> None:
        self._wf = wf
        self.inputs = inputs

    async def wait_signal(self, name: str, within: timedelta | None = None) -> Any:
        await workflow.wait_condition(lambda: bool(self._wf.inbox.get(name)), timeout=within)
        return self._wf.inbox[name].pop(0)

    async def sleep(self, seconds: float) -> None:
        await workflow.sleep(seconds)
