from typing import Any

import pytest
from agent_contracts.models import Role, Trust
from agent_contracts.testing import ctx
from agent_contracts.testing.agent_loop import AgentLoopContract, _node
from agent_runtime.engines.pydantic_ai import PydanticAILoopExecutor, to_messages
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart


class TestPydanticAILoop(AgentLoopContract):
    def make(self, model: Any, tools: Any, registry: Any) -> PydanticAILoopExecutor:
        return PydanticAILoopExecutor(model, tools, registry)


def test_history_becomes_platform_messages() -> None:
    history = [
        ModelResponse(parts=[ToolCallPart(tool_name="add", args={"a": 1}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="add", content=1, tool_call_id="c1")]),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    msgs = to_messages(history)
    assert [m.role for m in msgs] == [Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    assert msgs[0].tool_calls[0].arguments == {"a": 1}
    assert msgs[1].trust is Trust.UNTRUSTED


async def test_tools_needing_approval_are_refused() -> None:
    from agent_contracts.dummies.models import ScriptedModelClient
    from agent_contracts.dummies.tools import InMemoryToolRegistry, LocalToolExecutor

    reg = InMemoryToolRegistry()
    engine = PydanticAILoopExecutor(ScriptedModelClient(), LocalToolExecutor(reg), reg)
    with pytest.raises(ValueError, match="native agent loop"):
        await engine(_node(approval_tools=["add"]), {"input": {}}, ctx())
