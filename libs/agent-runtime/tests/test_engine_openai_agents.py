from typing import Any

import pytest
from agent_contracts.models import Role, Trust
from agent_contracts.testing import ctx
from agent_contracts.testing.agent_loop import AgentLoopContract, _node
from agent_runtime.engines.openai_agents import OpenAIAgentsLoopExecutor, to_messages


class TestOpenAIAgentsLoop(AgentLoopContract):
    def make(self, model: Any, tools: Any, registry: Any) -> OpenAIAgentsLoopExecutor:
        return OpenAIAgentsLoopExecutor(model, tools, registry)


def test_responses_items_become_platform_messages() -> None:
    items = [
        {"role": "user", "content": "hi"},
        {"type": "function_call", "call_id": "c1", "name": "add", "arguments": '{"a": 1}'},
        {"type": "function_call", "call_id": "c2", "name": "add", "arguments": '{"a": 2}'},
        {"type": "function_call_output", "call_id": "c1", "output": "1"},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "ok"}],
        },
    ]
    msgs = to_messages("sys", items)
    assert [m.role for m in msgs] == [
        Role.SYSTEM,
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
        Role.ASSISTANT,
    ]
    assert [c.id for c in msgs[2].tool_calls] == ["c1", "c2"], "consecutive calls form one turn"
    assert msgs[3].trust is Trust.UNTRUSTED


async def test_tools_needing_approval_are_refused() -> None:
    from agent_contracts.dummies.models import ScriptedModelClient
    from agent_contracts.dummies.tools import InMemoryToolRegistry, LocalToolExecutor

    reg = InMemoryToolRegistry()
    engine = OpenAIAgentsLoopExecutor(ScriptedModelClient(), LocalToolExecutor(reg), reg)
    with pytest.raises(ValueError, match="native agent loop"):
        await engine(_node(approval_tools=["add"]), {"input": {}}, ctx())
