"""Step 35: environment simulator (mock tools, seeded faults) and user simulator (persona, goal,
patience)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.models import Message, Role
from agent_contracts.testing.contracts import ToolContract, ctx
from agent_evals.checks import Observation, score
from agent_evals.env_sim import Environment, SimulatedFault, SimulatedTool, ToolDef
from agent_evals.tasks import Task
from agent_evals.user_sim import (
    Goal,
    ModelUser,
    Patience,
    Persona,
    ScriptedUser,
    UserProfile,
    converse,
    render_turn,
)

ORDER = {
    "name": "get_order",
    "input_schema": {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    },
    "output_schema": {"type": "object", "required": ["status"]},
    "responses": [{"when": {"order_id": "A-1"}, "result": {"status": "shipped"}}],
    "default": {"status": "unknown", "order_id": "{order_id}"},
    "example": {"order_id": "A-1"},
}


class TestSimulatedTool(ToolContract):
    def make(self) -> SimulatedTool:
        return Environment.from_defs([ORDER]).tools[0]


async def outcomes(env: Environment, n: int) -> list[str]:
    tool, out = env.tools[0], []
    for i in range(n):
        try:
            await tool.invoke({"order_id": f"B-{i}"}, ctx())
            out.append(env.log[-1].fault or "ok")
        except (SimulatedFault, TimeoutError):
            out.append(str(env.log[-1].fault))
    return out


async def test_answers_match_arguments_and_fill_placeholders() -> None:
    tool = Environment.from_defs([ORDER]).tools[0]
    assert await tool.invoke({"order_id": "A-1"}, ctx()) == {"status": "shipped"}
    assert await tool.invoke({"order_id": "Z-9"}, ctx()) == {"status": "unknown", "order_id": "Z-9"}


async def test_the_same_seed_gives_the_same_faults_and_another_seed_differs() -> None:
    faulty = ORDER | {"faults": {"error_rate": 0.3, "timeout_rate": 0.2, "malformed_rate": 0.2}}
    a = await outcomes(Environment.from_defs([faulty], seed=7), 40)
    b = await outcomes(Environment.from_defs([faulty], seed=7), 40)
    c = await outcomes(Environment.from_defs([faulty], seed=8), 40)
    assert a == b
    assert a != c
    assert {"ok", "error", "timeout", "malformed"} <= set(a)


async def test_outages_and_rate_limits_are_scheduled() -> None:
    env = Environment.from_defs([ORDER | {"faults": {"outage_calls": [2], "rate_limit_after": 3}}])
    assert await outcomes(env, 5) == ["ok", "outage", "ok", "rate_limited", "rate_limited"]
    assert env.faults() == [
        ("get_order", 2, "outage"),
        ("get_order", 4, "rate_limited"),
        ("get_order", 5, "rate_limited"),
    ]


def test_an_environment_loads_from_yaml(tmp_path: Path) -> None:
    (tmp_path / "env.yaml").write_text(json.dumps({"seed": 3, "tools": [ORDER]}))
    env = Environment.load(tmp_path / "env.yaml")
    assert env.seed == 3
    assert env.tool("get_order").spec.ref == "sim://env/get_order"
    with pytest.raises(ValueError, match="extra"):
        ToolDef.model_validate(ORDER | {"surprise": 1})


# ---------------------------------------------------------------- user simulator

REFUND = Goal(
    description="get a refund for a double charge",
    utterances=(
        "Hi, something is wrong with my account.",
        "I was charged twice on invoice INV-4411 and want a refund.",
    ),
    success={"contains": ["refund"]},  # type: ignore[arg-type]
)


def support_agent(message: str, history: Sequence[dict[str, str]]) -> Any:
    if "invoice" in message.lower():
        return {"answer": "Refund approved and processed."}
    return {"answer": "Our opening hours are 9 to 5."}


async def call(message: str, history: Sequence[dict[str, str]]) -> Any:
    return support_agent(message, history)


async def test_a_vague_customer_gets_there_on_the_second_turn() -> None:
    profile = UserProfile(persona=Persona(style="vague"), goal=REFUND)
    convo = await converse(ScriptedUser(profile), profile, call)
    assert convo.goal_met
    assert not convo.gave_up
    assert [t.user for t in convo.turns] == list(REFUND.utterances)
    assert convo.ended_because == "goal met"
    assert convo.patience_left == 0


async def test_a_direct_customer_says_it_all_at_once() -> None:
    profile = UserProfile(persona=Persona(style="direct"), goal=REFUND)
    convo = await converse(ScriptedUser(profile), profile, call)
    assert convo.goal_met
    assert len(convo.turns) == 1


async def test_an_impatient_customer_gives_up_after_one_unhelpful_reply() -> None:
    profile = UserProfile(
        persona=Persona(style="vague"), goal=REFUND, patience=Patience(tolerance=0)
    )
    convo = await converse(ScriptedUser(profile), profile, call)
    assert convo.gave_up
    assert not convo.goal_met
    assert (len(convo.turns), convo.ended_because) == (1, "out of patience")


async def test_patience_caps_the_number_of_turns() -> None:
    async def useless(message: str, history: Sequence[dict[str, str]]) -> Any:
        return "Let me check."

    profile = UserProfile(goal=REFUND, patience=Patience(max_turns=3, tolerance=9))
    convo = await converse(ScriptedUser(profile), profile, useless)
    assert (len(convo.turns), convo.ended_because) == (3, "no result in 3 turns")


async def test_the_customer_answers_questions_with_facts_they_know() -> None:
    asked: list[str] = []

    async def asks(message: str, history: Sequence[dict[str, str]]) -> Any:
        asked.append(message)
        if len(asked) == 1:
            return "Could you tell me your invoice number?"
        return "Refund issued." if "INV-4411" in message else "Sorry?"

    persona = Persona(style="vague", facts={"invoice number": "INV-4411"})
    profile = UserProfile(persona=persona, goal=REFUND)
    convo = await converse(ScriptedUser(profile), profile, asks)
    assert asked[1] == "My invoice number is INV-4411."
    assert convo.goal_met


async def test_a_model_plays_the_person_but_the_goal_check_decides_success() -> None:
    model = ScriptedModelClient(
        [
            Message(role=Role.ASSISTANT, content='{"message": "I need a refund", "stop": false}'),
            Message(role=Role.ASSISTANT, content="not json"),
        ]
    )
    profile = UserProfile(goal=REFUND, patience=Patience(tolerance=3))

    async def stalls(message: str, history: Sequence[dict[str, str]]) -> Any:
        return "Please hold."

    convo = await converse(ModelUser(model, profile), profile, stalls)
    assert [t.user for t in convo.turns] == ["I need a refund"]
    assert convo.gave_up
    assert convo.ended_because == "the person left"
    request = model.requests[0]
    assert request.temperature == 0
    assert "get a refund for a double charge" in request.messages[0].content


def test_turns_carry_the_earlier_conversation() -> None:
    assert render_turn("hi", []) == "hi"
    text = render_turn("refund please", [{"role": "user", "content": "hi"}])
    assert text.startswith("Earlier conversation:\nuser: hi")
    assert text.endswith("The customer now says: refund please")


def test_conversation_outcomes_are_scored() -> None:
    task = Task.model_validate(
        {
            "id": "c1",
            "app": "support-handoff",
            "input": {},
            "expected": {"conversation": {"goal_met": True, "max_turns": 2}},
        }
    )
    ok = Observation("succeeded", None, conversation={"goal_met": True, "turns": 2})
    assert score(task, ok)[0].passed
    slow = Observation(
        "succeeded", None, conversation={"goal_met": False, "turns": 3, "ended_because": "x"}
    )
    assert score(task, slow)[0].detail == "goal not met (x); 3 turns, at most 2"
    assert not score(task, Observation("succeeded", None))[0].passed
