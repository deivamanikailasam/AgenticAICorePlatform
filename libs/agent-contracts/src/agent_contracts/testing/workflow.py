"""``WorkflowRuntimeContract``: what a production runtime adapter must do beyond the basic
``RuntimeAdapterContract`` (Step 11). Every real adapter runs these same plans and must produce
the same results: that is how "the same spec passes the same tests on every adapter" is proven.

Subclasses implement ``make(functions=None)`` and merge ``WORKFLOW_FUNCTIONS`` into what they
pass to their adapter.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent_contracts.models import (
    ExecutablePlan,
    NodeKind,
    PlanEdge,
    PlanNode,
    RunEventType,
    RunStatus,
)
from agent_contracts.testing.contracts import RuntimeAdapterContract, ctx

BRANCH_SECONDS = 0.3


# Module-level so specs can name them by dotted path (agent_contracts.testing.workflow.slow_b).


def hello(state: dict[str, Any]) -> dict[str, Any]:
    return {"greeting": f"hello, {state.get('input', {}).get('name', 'world')}"}


def start(state: dict[str, Any]) -> str:
    return "A"


async def slow_b(state: dict[str, Any]) -> str:
    await asyncio.sleep(BRANCH_SECONDS)
    return "B"


async def slow_c(state: dict[str, Any]) -> str:
    await asyncio.sleep(BRANCH_SECONDS)
    return "C"


def combine(state: dict[str, Any]) -> str:
    return f"{state['start']}+{state['b']}+{state['c']}"


async def sleep_long(state: dict[str, Any]) -> str:
    await asyncio.sleep(30)
    return "never"


def boom(state: dict[str, Any]) -> None:
    raise RuntimeError("boom")


def size(state: dict[str, Any]) -> dict[str, Any]:
    return {"_outcome": "big" if state["input"].get("amount", 0) > 100 else "small"}


WORKFLOW_FUNCTIONS: dict[str, Any] = {
    "hello": hello,
    "start": start,
    "slow_b": slow_b,
    "slow_c": slow_c,
    "combine": combine,
    "boom": boom,
    "recover": lambda s: f"recovered from {s['risky']['error']}",
    "size": size,
    "big": lambda s: "big path",
    "small": lambda s: "small path",
    "again": lambda s: "looping",
    "sleep_long": sleep_long,
    "after_approval": lambda s: f"paid after {s['ask'].get('approved')}",
    "declined": lambda s: "declined",
}


def _fn(node_id: str, fn: str, kind: NodeKind = NodeKind.FUNCTION, **config: Any) -> PlanNode:
    return PlanNode(id=node_id, kind=kind, config={"fn": fn, **config})


def fan_out_plan() -> ExecutablePlan:
    """start -> (b, c) in parallel -> join -> combine."""
    return ExecutablePlan(
        name="fan-out",
        nodes=(
            _fn("start", "start"),
            _fn("b", "slow_b"),
            _fn("c", "slow_c"),
            PlanNode(id="join", kind=NodeKind.JOIN),
            _fn("combine", "combine"),
        ),
        edges=(
            PlanEdge(source="start", target="b"),
            PlanEdge(source="start", target="c"),
            PlanEdge(source="b", target="join"),
            PlanEdge(source="c", target="join"),
            PlanEdge(source="join", target="combine"),
        ),
        entry="start",
        exits=("combine",),
    )


def routing_plan() -> ExecutablePlan:
    return ExecutablePlan(
        name="routing",
        nodes=(_fn("size", "size"), _fn("big", "big"), _fn("small", "small")),
        edges=(
            PlanEdge(source="size", target="big", condition="big"),
            PlanEdge(source="size", target="small", condition="small"),
        ),
        entry="size",
        exits=("big", "small"),
    )


def error_plan(handled: bool) -> ExecutablePlan:
    nodes = [_fn("risky", "boom")]
    edges = []
    if handled:
        nodes.append(_fn("recover", "recover"))
        edges.append(PlanEdge(source="risky", target="recover", condition="on_error"))
    return ExecutablePlan(
        name=f"error-{'handled' if handled else 'unhandled'}",
        nodes=tuple(nodes),
        edges=tuple(edges),
        entry="risky",
        exits=(nodes[-1].id,),
    )


def loop_plan(max_steps: int) -> ExecutablePlan:
    return ExecutablePlan(
        name="loop",
        nodes=(_fn("again", "again"),),
        edges=(PlanEdge(source="again", target="again"),),
        entry="again",
        exits=("again",),
        metadata={"budgets": {"max_steps": max_steps}},
    )


def slow_plan() -> ExecutablePlan:
    return ExecutablePlan(
        name="slow", nodes=(_fn("wait", "sleep_long"),), entry="wait", exits=("wait",)
    )


def approval_branch_plan() -> ExecutablePlan:
    return ExecutablePlan(
        name="approval-branch",
        nodes=(
            PlanNode(id="ask", kind=NodeKind.APPROVAL, config={"tool": "pay"}),
            _fn("pay", "after_approval"),
            _fn("declined", "declined"),
        ),
        edges=(
            PlanEdge(source="ask", target="pay", condition="ok"),
            PlanEdge(source="ask", target="declined", condition="rejected"),
        ),
        entry="ask",
        exits=("pay", "declined"),
    )


class WorkflowRuntimeContract(RuntimeAdapterContract):
    def make(self, functions: dict[str, Any] | None = None) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def test_parallel_branches_run_concurrently_and_join_waits(self) -> None:
        rt = self.make()
        handle = await rt.build(fan_out_plan())
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await rt.invoke(handle, {}, ctx())
        elapsed = loop.time() - started
        assert result.status is RunStatus.SUCCEEDED, result
        assert result.output == "A+B+C", "the join waited for both branches"
        assert elapsed < BRANCH_SECONDS * 1.8, f"branches ran one after another ({elapsed:.2f}s)"

    async def test_routes_by_outcome(self) -> None:
        rt = self.make()
        handle = await rt.build(routing_plan())
        big = await rt.invoke(handle, {"amount": 500}, ctx())
        small = await rt.invoke(handle, {"amount": 5}, ctx())
        assert big.output == "big path"
        assert small.output == "small path"
        assert "small" not in big.state.values
        assert "big" not in small.state.values

    async def test_error_edge_handles_a_failure(self) -> None:
        rt = self.make()
        handle = await rt.build(error_plan(handled=True))
        result = await rt.invoke(handle, {}, ctx())
        assert result.status is RunStatus.SUCCEEDED
        assert result.output == "recovered from RuntimeError: boom"

    async def test_unhandled_failure_fails_the_run(self) -> None:
        rt = self.make()
        handle = await rt.build(error_plan(handled=False))
        c = ctx()
        result = await rt.invoke(handle, {}, c)
        assert result.status is RunStatus.FAILED
        assert "boom" in (result.state.error or "")
        assert (await rt.get_state(c.run_id)).status is RunStatus.FAILED

    async def test_max_steps_stops_a_loop(self) -> None:
        rt = self.make()
        handle = await rt.build(loop_plan(max_steps=5))
        result = await rt.invoke(handle, {}, ctx())
        assert result.status is RunStatus.STOPPED
        assert "max_steps" in (result.state.stop_reason or "")

    async def test_cancel_stops_a_running_node(self) -> None:
        rt = self.make()
        handle = await rt.build(slow_plan())
        c = ctx()
        task = asyncio.create_task(rt.invoke(handle, {}, c))
        await asyncio.sleep(0.3)
        loop = asyncio.get_running_loop()
        started = loop.time()
        await rt.cancel(c.run_id)
        assert loop.time() - started < 5
        assert (await rt.get_state(c.run_id)).status is RunStatus.CANCELLED
        result = await asyncio.wait_for(task, 5)  # the caller gets a result, not an exception
        assert result.status is RunStatus.CANCELLED

    async def test_rejected_approval_takes_the_rejected_branch(self) -> None:
        rt = self.make()
        handle = await rt.build(approval_branch_plan())
        c = ctx()
        first = await rt.invoke(handle, {}, c)
        assert first.status is RunStatus.INTERRUPTED
        assert first.state.next_nodes == ("ask",)
        done = await rt.resume(handle, c.run_id, {"approved": False}, c)
        assert done.status is RunStatus.SUCCEEDED
        assert done.output == "declined"

    async def test_stream_reports_every_node_with_its_outcome(self) -> None:
        rt = self.make()
        handle = await rt.build(error_plan(handled=True))
        events = [e async for e in rt.stream(handle, {}, ctx())]
        started = [e.node_id for e in events if e.type is RunEventType.NODE_STARTED]
        finished = {
            e.node_id: e.data.get("outcome") for e in events if e.type is RunEventType.NODE_FINISHED
        }
        assert started == ["risky", "recover"]
        assert finished == {"risky": "error", "recover": "ok"}

    async def test_same_plan_same_inputs_same_path(self) -> None:
        rt = self.make()
        handle = await rt.build(fan_out_plan())
        a = await rt.invoke(handle, {"x": 1}, ctx())
        b = await rt.invoke(handle, {"x": 1}, ctx())
        assert a.output == b.output
        assert a.state.values.keys() == b.state.values.keys()
