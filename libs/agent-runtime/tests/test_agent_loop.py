"""The ReAct agent loop (Step 13) with scripted models and real platform tools."""

import asyncio
import json
from typing import Any

import pytest
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import Tool
from agent_contracts.models import (
    Message,
    ModelCapabilities,
    NodeKind,
    PlanNode,
    Role,
    SideEffect,
    ToolCall,
    ToolSpec,
    Trust,
)
from agent_contracts.testing import ctx
from agent_contracts.testing.agent_loop import AgentLoopContract
from agent_runtime.agent_loop import AgentLoopExecutor, MalformedResponseError, parse_text_react
from agent_runtime.nodes import StopRun
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.builtin import CalculatorTool, WordCountTool
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry


class SlowLookup(Tool):
    """A read-only tool that takes a while, to show read calls overlap."""

    def __init__(self, name: str = "lookup", side_effect: SideEffect = SideEffect.READ) -> None:
        self._spec = ToolSpec(
            name=name,
            ref=f"local://test/{name}",
            description="Look up a key.",
            input_schema={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
                "additionalProperties": False,
            },
            side_effect=side_effect,
            idempotent=side_effect is SideEffect.READ,
        )
        self.calls: list[str] = []

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def invoke(self, arguments: Any, run_ctx: Any) -> Any:
        self.calls.append(arguments["key"])
        await asyncio.sleep(0.3)
        return {"key": arguments["key"], "value": f"value-of-{arguments['key']}"}


def _call(name: str, **args: Any) -> ToolCall:
    return ToolCall(name=name, arguments=args)


def _ask(*calls: ToolCall) -> Message:
    return Message(role=Role.ASSISTANT, tool_calls=calls)


def _say(text: str) -> Message:
    return Message(role=Role.ASSISTANT, content=text)


async def _loop(
    model: Any, *extra: Tool, **config: Any
) -> tuple[AgentLoopExecutor, PlanNode, MemoryAuditSink]:
    reg = InMemoryToolRegistry()
    for t in (CalculatorTool(), WordCountTool(), *extra):
        await reg.register(t)
    audit = MemoryAuditSink()
    tools = AuthorisingToolExecutor(
        PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), audit
    )
    names = ["calculator", "word_count", *(t.spec.name for t in extra)]
    node = PlanNode(
        id="agent.loop",
        kind=NodeKind.AGENT,
        config={
            "loop": "react",
            "tools": names,
            "model": "default",
            "max_iterations": config.pop("max_iterations", 6),
            "approval_tools": config.pop("approval_tools", []),
            "approval_node": "agent.approval",
            "config": config,
        },
    )
    return AgentLoopExecutor(model, tools, reg), node, audit


VALUES: dict[str, Any] = {"input": {"question": "What is 17 * 23?"}}


async def test_solves_a_tool_task_and_feeds_back_untrusted_observations() -> None:
    model = ScriptedModelClient([_ask(_call("calculator", expression="17 * 23")), _say("391")])
    loop, node, audit = await _loop(model)
    out = await loop(node, VALUES, ctx())
    assert out == {"answer": "391", "steps": 2}
    first, second = model.requests
    assert {t.name for t in first.tools} == {"calculator", "word_count"}
    observation = second.messages[-1]
    assert observation.role is Role.TOOL
    assert observation.trust is Trust.UNTRUSTED
    assert json.loads(observation.content) == {"result": 391.0}
    assert (await audit.read())[0].event.payload["decision"] == "allow"


async def test_read_calls_in_one_turn_run_in_parallel() -> None:
    lookup = SlowLookup()
    model = ScriptedModelClient(
        [_ask(_call("lookup", key="a"), _call("lookup", key="b")), _say("done")]
    )
    loop, node, _ = await _loop(model, lookup)
    started = asyncio.get_running_loop().time()
    await loop(node, VALUES, ctx())
    assert asyncio.get_running_loop().time() - started < 0.55
    assert sorted(lookup.calls) == ["a", "b"]


async def test_write_calls_run_one_at_a_time_in_order_with_their_own_keys() -> None:
    writer = SlowLookup("record", SideEffect.WRITE)
    model = ScriptedModelClient(
        [_ask(_call("record", key="x"), _call("record", key="y")), _say("ok")]
    )
    loop, node, _ = await _loop(model, writer)
    started = asyncio.get_running_loop().time()
    c = ctx()
    await loop(node, VALUES, c)
    assert asyncio.get_running_loop().time() - started >= 0.6
    assert writer.calls == ["x", "y"]


async def test_malformed_calls_are_repaired() -> None:
    model = ScriptedModelClient(
        [
            _ask(_call("calc", expression="1+1")),  # unknown tool
            _ask(_call("calculator", wrong="1+1")),  # schema-invalid arguments
            _ask(_call("calculator", _unparseable="{1+1")),  # gateway could not parse
            _ask(_call("calculator", expression="1+1")),
            _say("2"),
        ]
    )
    loop, node, _ = await _loop(model, max_repairs=3)
    out = await loop(node, VALUES, ctx())
    assert out["answer"] == "2"
    errors = [
        json.loads(m.content)["error"] for m in model.requests[1].messages if m.role is Role.TOOL
    ]
    assert "unknown tool 'calc'" in errors[0]


async def test_malformed_calls_fail_cleanly_after_the_repair_limit() -> None:
    model = ScriptedModelClient([_ask(_call("nope"))] * 5)
    loop, node, _ = await _loop(model, max_repairs=2)
    with pytest.raises(ValueError, match="still malformed after 2 repairs"):
        await loop(node, VALUES, ctx())
    assert len(model.requests) == 3


SCHEMA = {
    "type": "object",
    "properties": {"total": {"type": "number"}},
    "required": ["total"],
    "additionalProperties": False,
}


async def test_final_answer_is_validated_with_one_repair_retry() -> None:
    model = ScriptedModelClient([_say("the total is 391"), _say('```json\n{"total": 391}\n```')])
    loop, node, _ = await _loop(model, output_schema=SCHEMA)
    out = await loop(node, VALUES, ctx())
    assert out["answer"] == {"total": 391}
    assert "not valid" in model.requests[1].messages[-1].content


async def test_final_answer_still_invalid_fails() -> None:
    model = ScriptedModelClient([_say("nope"), _say('{"total": "many"}')])
    loop, node, _ = await _loop(model, output_schema=SCHEMA)
    with pytest.raises(ValueError, match="still invalid after a repair"):
        await loop(node, VALUES, ctx())


async def test_loop_stops_at_max_steps() -> None:
    model = ScriptedModelClient([_ask(_call("calculator", expression="1+1"))] * 10)
    loop, node, _ = await _loop(model, max_iterations=3)
    with pytest.raises(StopRun, match=r"max_steps \(3\) reached") as stopped:
        await loop(node, VALUES, ctx())
    assert len(model.requests) == 3
    assert stopped.value.partial["steps"] == 3


class TextOnlyModel(ScriptedModelClient):
    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(tool_calling=False)


async def test_text_react_for_models_without_tool_calling() -> None:
    model = TextOnlyModel(
        [
            _say("Thought: I should compute.\nAction: calculator\nAction Input: {broken"),
            _say('Thought: compute.\nAction: calculator\nAction Input: {"expression": "17*23"}'),
            _say("Final Answer: 391"),
        ]
    )
    loop, node, _ = await _loop(model)
    out = await loop(node, VALUES, ctx())
    assert out["answer"] == "391"
    first = model.requests[0]
    assert first.tools == ()
    assert "Action Input" in first.messages[0].content
    assert "calculator" in first.messages[0].content
    assert "not valid JSON" in model.requests[1].messages[-1].content
    assert model.requests[2].messages[-1].content.startswith("Observation from calculator: ")
    assert "391" in model.requests[2].messages[-1].content


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('Action: calculator\nAction Input: {"expression": "2"}', ToolCall),
        ("Thought: done\nFinal Answer: 42", "42"),
    ],
)
def test_parse_text_react(text: str, expected: Any) -> None:
    parsed = parse_text_react(text)
    if expected is ToolCall:
        assert isinstance(parsed, ToolCall)
        assert parsed.arguments == {"expression": "2"}
    else:
        assert parsed == expected


def test_parse_text_react_rejects_free_text() -> None:
    with pytest.raises(MalformedResponseError):
        parse_text_react("I think the answer might be 42")


async def test_tool_needing_approval_pauses_then_runs_once_approved() -> None:
    writer = SlowLookup("record", SideEffect.WRITE)
    model = ScriptedModelClient([_ask(_call("record", key="pay-1")), _say("paid")])
    loop, node, _ = await _loop(model, writer, approval_tools=["record"])
    c = ctx()
    paused = await loop(node, VALUES, c)
    assert paused["_outcome"] == "needs_approval"
    assert paused["pending"][0]["name"] == "record"
    assert writer.calls == []
    values = {**VALUES, node.id: paused, "agent.approval": {"approved": True}}
    done = await loop(node, values, c)
    assert done["answer"] == "paid"
    assert writer.calls == ["pay-1"]


async def test_rejected_approval_is_reported_to_the_model_and_nothing_runs() -> None:
    writer = SlowLookup("record", SideEffect.WRITE)
    model = ScriptedModelClient([_ask(_call("record", key="pay-1")), _say("not paid")])
    loop, node, _ = await _loop(model, writer, approval_tools=["record"])
    c = ctx()
    paused = await loop(node, VALUES, c)
    values = {**VALUES, node.id: paused, "agent.approval": {"approved": False}}
    done = await loop(node, values, c)
    assert done["answer"] == "not paid"
    assert writer.calls == []
    assert "rejected" in model.requests[-1].messages[-1].content


class TestNativeLoopContract(AgentLoopContract):
    def make(self, model: Any, tools: Any, registry: Any) -> AgentLoopExecutor:
        return AgentLoopExecutor(model, tools, registry)
