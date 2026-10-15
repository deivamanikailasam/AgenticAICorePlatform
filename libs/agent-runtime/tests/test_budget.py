"""Budgets and loop control (Step 26): the platform controller and detector."""

import pytest
from agent_contracts.models import BudgetCharge, BudgetLimits, StepObservation, Usage
from agent_contracts.testing import BudgetControllerContract, LoopDetectorContract
from agent_runtime.budget import PlatformBudgetController, PlatformLoopDetector


class TestPlatformBudgetController(BudgetControllerContract):
    def make(self) -> PlatformBudgetController:
        return PlatformBudgetController()


class TestPlatformLoopDetector(LoopDetectorContract):
    def make(self) -> PlatformLoopDetector:
        return PlatformLoopDetector()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_cost_names_the_limit_with_the_numbers() -> None:
    b = PlatformBudgetController()
    await b.open("r", BudgetLimits(max_cost_aud=0.01))
    s = await b.charge("r", BudgetCharge(usage=Usage(cost_aud=0.012)))
    assert s.tripped == "max_cost_aud"
    assert s.detail == "max_cost_aud: AUD 0.0120 over the limit of 0.01"


async def test_per_tool_limit() -> None:
    b = PlatformBudgetController()
    await b.open("r", BudgetLimits(max_calls_per_tool={"search": 2}))
    for _ in range(2):
        assert (await b.charge("r", BudgetCharge(tool_calls=1, tool="search"))).ok
    assert (await b.charge("r", BudgetCharge(tool_calls=5, tool="clock"))).ok
    s = await b.charge("r", BudgetCharge(tool_calls=1, tool="search"))
    assert s.tripped == "max_calls_per_tool"
    assert "3 calls to search" in (s.detail or "")


async def test_wall_clock_counts_from_open_even_without_reported_time() -> None:
    clock = Clock()
    b = PlatformBudgetController(clock)
    await b.open("r", BudgetLimits(max_wall_seconds=10))
    clock.now = 11
    s = await b.charge("r", BudgetCharge(steps=1))
    assert s.tripped == "max_wall_seconds"


async def test_delegation_depth_and_parent_trip_stop_children() -> None:
    b = PlatformBudgetController()
    await b.open("root", BudgetLimits(max_delegation_depth=1, max_cost_aud=0.05))
    await b.open("root/a", BudgetLimits(), parent_run_id="root")
    deep = await b.open("root/a/b", BudgetLimits(), parent_run_id="root/a")
    assert deep.tripped == "max_delegation_depth"
    assert deep.depth == 2
    await b.charge("root/a", BudgetCharge(usage=Usage(cost_aud=0.06)))
    sibling = await b.open("root/c", BudgetLimits(), parent_run_id="root")
    assert not sibling.ok, "once the parent trips, no child may continue"
    assert sibling.tripped == "max_cost_aud"


async def test_charging_an_unknown_run_is_an_error() -> None:
    with pytest.raises(KeyError):
        await PlatformBudgetController().charge("nope", BudgetCharge(steps=1))


def test_no_progress_and_oscillation() -> None:
    d = PlatformLoopDetector(no_progress=4, oscillation=3)
    verdicts = [
        d.observe("r", StepObservation(kind="model_call", signature=f"s{i}", progress=False))
        for i in range(4)
    ]
    assert [v.loop for v in verdicts] == [False, False, False, True]
    assert verdicts[-1].reason == "no progress for 4 steps"
    d2 = PlatformLoopDetector(oscillation=3)
    seq = ["plan-a", "plan-b"] * 3
    out = [d2.observe("r", StepObservation(kind="plan", signature=s)) for s in seq]
    assert [v.loop for v in out] == [False] * 5 + [True]
    assert "oscillating" in (out[-1].reason or "")
