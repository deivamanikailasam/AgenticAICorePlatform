"""Step 35: simulated people in multi-turn conversations through the run API, and a simulated
environment's faults through the platform's tool executor.

The support team is played by Step 23's rule-based stand-ins, so these test the simulators and
the platform's mechanics (one run per turn on one thread, delegations, scoring), not model
quality.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from agent_api.eval_apps import SpecHarness
from agent_api.eval_runner import Provider, run_tasks
from agent_compiler.team_eval import StandIn, respond
from agent_contracts.models import RunContext, ToolCall, ToolErrorCode
from agent_evals.checks import score, success
from agent_evals.env_sim import Environment
from agent_evals.generators import build_conversations, conversations_path
from agent_evals.tasks import load_tasks
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry


@pytest.fixture(autouse=True)
def _presidio(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from tests.stack.fakes.presidio_fake import presidio

    with presidio() as url:
        monkeypatch.setenv("P0_PRESIDIO_ANALYZER_URL", url)
        monkeypatch.setenv("P0_PRESIDIO_ANONYMIZER_URL", url)
        yield


def test_the_conversation_set_is_current() -> None:
    tasks = load_tasks(conversations_path("support-handoff"))
    assert [t.model_dump() for t in tasks] == [
        t.model_dump() for t in build_conversations("support-handoff")
    ], "just eval-tasks"
    assert len(tasks) == 24


async def test_simulated_customers_talk_to_the_support_team_over_several_turns() -> None:
    tasks = load_tasks(conversations_path("support-handoff"))
    # one of each: vague/direct x tolerance 0/1, billing and tech
    sample = [tasks[i] for i in (0, 1, 2, 3, 4, 5)]
    provider = Provider("scripted", lambda spec: StandIn(spec.name, respond))
    results = await run_tasks(SpecHarness("support-handoff"), sample, provider)
    by_id = {t.id: (t, obs) for t, obs, _ in results}
    for task, obs in by_id.values():
        scores = score(task, obs)
        assert success(scores), (task.id, [s.detail for s in scores if not s.passed])
    vague_patient = by_id["conversation-billing-004"][1].conversation
    assert vague_patient is not None
    assert (vague_patient["turns"], vague_patient["goal_met"]) == (2, True)
    assert vague_patient["transcript"][0]["role"] == "user"
    assert "Refund approved" in vague_patient["transcript"][-1]["content"]
    impatient = by_id["conversation-billing-000"][1].conversation
    assert impatient is not None
    assert (impatient["turns"], impatient["ended_because"]) == (1, "out of patience")
    # every turn was a run on the conversation's own thread; delegations add up across turns
    assert by_id["conversation-billing-004"][1].delegations == ["triage", "triage", "billing"]


async def test_seeded_faults_reach_the_agent_as_platform_errors() -> None:
    env = Environment.from_defs(
        [
            {
                "name": "get_order",
                "input_schema": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                },
                "default": {"status": "shipped"},
                "faults": {"outage_calls": [1], "rate_limit_after": 3},
            },
            {
                "name": "get_invoice",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object", "required": ["total"]},
                "default": {"total": 10},
                "faults": {"malformed_rate": 1.0},
            },
            {
                "name": "slow_lookup",
                "input_schema": {"type": "object"},
                "default": {},
                "faults": {"timeout_rate": 1.0},
            },
        ],
        seed=35,
    )
    registry = InMemoryToolRegistry()
    await env.register(registry)
    executor = PlatformToolExecutor(registry)
    ctx = RunContext(tenant_id="t1", user_id="u1", agent_id="ops")

    async def run(name: str) -> tuple[bool, ToolErrorCode | None]:
        result = await executor.execute(ToolCall(name=name, arguments={"order_id": "A"}), ctx)
        return result.ok, result.error_code

    assert await run("get_order") == (False, ToolErrorCode.FAILED)  # call 1: the outage
    assert await run("get_order") == (True, None)
    assert await run("get_invoice") == (False, ToolErrorCode.FAILED)  # breaks the output schema
    assert await run("slow_lookup") == (False, ToolErrorCode.TIMEOUT)
    assert await run("get_order") == (True, None)
    assert await run("get_order") == (False, ToolErrorCode.FAILED)  # over the rate limit
    assert env.faults()[0] == ("get_order", 1, "outage")
    assert ("get_order", 4, "rate_limited") in env.faults()
