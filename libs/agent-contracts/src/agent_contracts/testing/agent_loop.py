"""``AgentLoopContract``: what every agent-loop engine must do (Step 13).

An engine is a callable ``(node, values, ctx) -> value`` for AGENT nodes with ``loop`` config
(the platform's native loop, or one that runs on a framework such as the OpenAI Agents SDK or
Pydantic AI). Each engine is built from the platform's model client and tool executor, so model
access and tool policy are the platform's whatever runs the loop.

Subclasses implement ``make(model, tools, registry)``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.tools import AddTool, EchoTool, InMemoryToolRegistry, LocalToolExecutor
from agent_contracts.interfaces import AgentLoop, StopRun
from agent_contracts.models import Message, NodeKind, PlanNode, Role, ToolCall, Trust
from agent_contracts.testing.contracts import ctx

SCHEMA = {
    "type": "object",
    "properties": {"total": {"type": "number"}},
    "required": ["total"],
    "additionalProperties": False,
}


def _node(**config: Any) -> PlanNode:
    return PlanNode(
        id="agent.loop",
        kind=NodeKind.AGENT,
        config={
            "loop": "react",
            "tools": ["add", "echo"],
            "model": "default",
            "max_iterations": config.pop("max_iterations", 6),
            "approval_tools": config.pop("approval_tools", []),
            "approval_node": "agent.approval",
            "config": config,
        },
    )


def _ask(name: str, **args: Any) -> Message:
    return Message(role=Role.ASSISTANT, tool_calls=(ToolCall(name=name, arguments=args),))


def _say(text: str) -> Message:
    return Message(role=Role.ASSISTANT, content=text)


VALUES = {"input": {"question": "What is 2 + 3?"}}


class AgentLoopContract:
    interface = AgentLoop

    def make(self, model: Any, tools: Any, registry: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def _engine(self, script: list[Message]) -> tuple[Any, ScriptedModelClient]:
        registry = InMemoryToolRegistry()
        await registry.register(AddTool())
        await registry.register(EchoTool())
        model = ScriptedModelClient(script)
        engine = self.make(model, LocalToolExecutor(registry), registry)
        assert isinstance(engine, AgentLoop)
        return engine, model

    async def test_uses_a_tool_and_answers(self) -> None:
        engine, model = await self._engine([_ask("add", a=2, b=3), _say("5")])
        out = await engine(_node(), VALUES, ctx())
        assert out["answer"] == "5"
        assert {t.name for t in model.requests[0].tools} == {"add", "echo"}
        observation = model.requests[1].messages[-1]
        assert observation.role is Role.TOOL
        assert json.loads(observation.content) == 5
        assert observation.trust is Trust.UNTRUSTED, (
            "tool output reaches the model as untrusted data"
        )

    async def test_task_comes_from_the_input(self) -> None:
        engine, model = await self._engine([_say("ok")])
        await engine(_node(), VALUES, ctx())
        user = [m for m in model.requests[0].messages if m.role is Role.USER]
        assert "What is 2 + 3?" in user[0].content

    async def test_structured_final_answer(self) -> None:
        engine, _ = await self._engine([_ask("add", a=2, b=3), _say('{"total": 5}')])
        out = await engine(_node(output_schema=SCHEMA), VALUES, ctx())
        assert out["answer"] == {"total": 5}

    async def test_stops_at_max_steps(self) -> None:
        engine, model = await self._engine([_ask("add", a=1, b=1)] * 10)
        with pytest.raises(StopRun, match="max_steps"):
            await engine(_node(max_iterations=3), VALUES, ctx())
        assert len(model.requests) <= 4

    async def test_unknown_tool_does_not_run_anything_and_ends_cleanly(self) -> None:
        engine, _ = await self._engine([_ask("delete_everything")] * 5 + [_say("done")])
        outcome: Any
        try:
            outcome = await engine(_node(), VALUES, ctx())
        except (ValueError, RuntimeError) as exc:  # failing cleanly is acceptable
            outcome = exc
        if isinstance(outcome, Exception):
            assert any(w in str(outcome) for w in ("delete_everything", "malformed", "tool"))
        else:
            assert outcome["answer"] == "done", "or it was repaired and finished"
