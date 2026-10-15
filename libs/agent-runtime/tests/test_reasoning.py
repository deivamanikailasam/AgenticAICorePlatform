"""LLM planner and critic (Step 14): the planner/critic slot contracts with scripted models."""

import json
from typing import Any

import pytest
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.models import Message, Role
from agent_contracts.testing import CriticContract, PlannerContract, ctx
from agent_runtime.reasoning import LlmCritic, LlmPlanner


def say(obj: Any) -> Message:
    return Message(role=Role.ASSISTANT, content=obj if isinstance(obj, str) else json.dumps(obj))


class TestLlmPlannerContract(PlannerContract):
    def make(self) -> LlmPlanner:
        plan = {
            "steps": [
                {"id": "E1", "description": "find sources", "tool": "search"},
                {"id": "E2", "description": "write it", "tool": "write", "depends_on": ["E1"]},
            ]
        }
        return LlmPlanner(ScriptedModelClient([say(plan)]))


class TestLlmCriticContract(CriticContract):
    def make(self) -> LlmCritic:
        return LlmCritic(
            ScriptedModelClient([say({"score": 0.9, "passed": True, "feedback": "ok"})])
        )


async def test_planner_refuses_tools_it_was_not_given() -> None:
    plan = {"steps": [{"id": "E1", "description": "pay", "tool": "post_payment"}]}
    with pytest.raises(ValueError, match="not available"):
        await LlmPlanner(ScriptedModelClient([say(plan)])).plan("pay", ["calculator"], ctx())
