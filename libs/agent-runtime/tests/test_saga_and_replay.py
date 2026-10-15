"""Saga compensation and journal replay at the plan level, on both adapters (Step 12)."""

from typing import Any

import pytest
from agent_contracts.models import ExecutablePlan, NodeKind, PlanEdge, PlanNode, RunStatus
from agent_contracts.testing import ctx
from agent_contracts.testing import workflow as wf
from agent_runtime.adapters.agent_framework import AgentFrameworkAdapter
from agent_runtime.adapters.langgraph import LangGraphAdapter
from agent_runtime.nodes import NodeOutcome, NodeRunner, RunJournal

FUNCS: dict[str, Any] = {
    "reserve": wf.reserve,
    "charge": wf.charge,
    "ship": wf.ship,
    "release": wf.release,
    "refund": wf.refund,
}
ADAPTERS = [LangGraphAdapter, AgentFrameworkAdapter]


def _fn(node_id: str, fn: str, **config: Any) -> PlanNode:
    return PlanNode(id=node_id, kind=NodeKind.FUNCTION, config={"fn": fn, **config})


SAGA = ExecutablePlan(
    name="saga",
    nodes=(
        _fn("reserve", "reserve", compensation="release"),
        _fn("charge", "charge", compensation="refund"),
        _fn("ship", "ship"),
        _fn("release", "release"),
        _fn("refund", "refund"),
    ),
    edges=(PlanEdge(source="reserve", target="charge"), PlanEdge(source="charge", target="ship")),
    entry="reserve",
    exits=("ship",),
)


@pytest.mark.parametrize("cls", ADAPTERS)
async def test_failure_compensates_finished_steps_newest_first(cls: Any) -> None:
    wf.SAGA_LOG.clear()
    rt = cls(runner=NodeRunner(FUNCS))
    handle = await rt.build(SAGA)
    result = await rt.invoke(handle, {}, ctx())
    assert result.status is RunStatus.FAILED
    assert wf.SAGA_LOG[-2:] == ["refund (undoing charge)", "release (undoing reserve)"]
    assert "compensated: refund, release" in (result.state.error or "")


@pytest.mark.parametrize("cls", ADAPTERS)
async def test_replay_returns_recorded_results_without_running(cls: Any) -> None:
    wf.SAGA_LOG.clear()
    recorder = NodeRunner(FUNCS)
    first_ctx = ctx()
    rt = cls(runner=recorder)
    await rt.invoke(await rt.build(SAGA), {}, first_ctx)
    executed = list(wf.SAGA_LOG)
    wf.SAGA_LOG.clear()

    replay = cls(runner=NodeRunner(FUNCS, replay=recorder.journal(first_ctx.run_id)))
    second = await replay.invoke(await replay.build(SAGA), {}, ctx())
    assert wf.SAGA_LOG == [], "nothing re-executed"
    assert second.status is RunStatus.FAILED
    assert executed[-1] == "release (undoing reserve)"
    assert second.state.values["release"] == "released"


def test_journal_round_trips_through_json() -> None:
    j = RunJournal("r1")
    j.record("a", NodeOutcome("ok", {"x": 1}))
    j.record("a", NodeOutcome("error", {"error": "e"}, "e"))
    back = RunJournal.from_json(j.to_json())
    assert back.next("a") == NodeOutcome("ok", {"x": 1})
    assert back.next("a").outcome == "error"
    with pytest.raises(LookupError, match="replay diverged"):
        back.next("a")
