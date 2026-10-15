"""Run events from inside a run (Step 17): node, tool and text events through a bound sink."""

import json

from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    Message,
    NodeKind,
    PlanNode,
    Role,
    RunEvent,
    RunEventType,
    ToolCall,
)
from agent_contracts.testing import ctx
from agent_runtime.events import (
    EventingNodeRunner,
    EventingToolExecutor,
    StreamingModelClient,
    current_sink,
)
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.builtin import CalculatorTool
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry


class Collect:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    async def __call__(self, event: RunEvent) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.type.value for e in self.events]


async def test_wrappers_are_pass_through_without_a_sink() -> None:
    model = StreamingModelClient(ScriptedModelClient([Message(role=Role.ASSISTANT, content="hi")]))
    reply = await model.chat(
        ChatRequest(model="default", messages=(Message(role=Role.USER, content="x"),))
    )
    assert reply.message.content == "hi"


async def test_node_runner_emits_start_finish_and_the_new_value() -> None:
    sink = Collect()
    token = current_sink.set(sink)
    try:
        runner = EventingNodeRunner({"double": lambda v: v["input"]["n"] * 2})
        node = PlanNode(id="calc", kind=NodeKind.FUNCTION, config={"fn": "double"})
        await runner.run(node, {"input": {"n": 21}}, ctx())
    finally:
        current_sink.reset(token)
    assert sink.types() == ["node_started", "node_finished", "state_delta"]
    assert sink.events[1].data == {"outcome": "ok", "error": None}
    assert sink.events[2].data == {"values": {"calc": 42}}


async def test_tool_calls_are_announced_and_reported() -> None:
    reg = InMemoryToolRegistry()
    await reg.register(CalculatorTool())
    tools = EventingToolExecutor(
        AuthorisingToolExecutor(
            PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), MemoryAuditSink()
        )
    )
    assert tools.registry is reg
    sink = Collect()
    token = current_sink.set(sink)
    try:
        call = ToolCall(name="calculator", arguments={"expression": "6*7"})
        await tools.execute(call, ctx())
    finally:
        current_sink.reset(token)
    started, finished = sink.events
    assert (started.type, finished.type) == (
        RunEventType.TOOL_CALL_STARTED,
        RunEventType.TOOL_CALL_FINISHED,
    )
    assert started.data == {
        "tool_call_id": call.id,
        "tool": "calculator",
        "arguments": {"expression": "6*7"},
    }
    assert (finished.data["ok"], finished.data["output"]) == (True, {"result": 42.0})


class Chunky(ScriptedModelClient):
    """Streams each word as its own chunk, like a model client streaming tokens."""

    async def stream(self, request: ChatRequest):  # type: ignore[no-untyped-def]
        response = await self.chat(request)
        for i, word in enumerate(response.message.content.split(" ")):
            yield ChatChunk(delta=word if i == 0 else f" {word}")
        yield ChatChunk(finish_reason="stop")


async def test_plain_text_replies_stream_token_by_token_and_json_replies_do_not() -> None:
    inner = Chunky(
        [
            Message(role=Role.ASSISTANT, content="Hello there"),
            Message(role=Role.ASSISTANT, content='{"a": 1}'),
        ]
    )
    model = StreamingModelClient(inner)
    sink = Collect()
    token = current_sink.set(sink)
    try:
        plain = ChatRequest(
            model="default",
            messages=(Message(role=Role.USER, content="hi"),),
            metadata={"run_id": "r1"},
        )
        reply = await model.chat(plain)
        structured = plain.model_copy(update={"response_schema": {"type": "object"}})
        await model.chat(structured)
    finally:
        current_sink.reset(token)
    assert reply.message.content == "Hello there"
    deltas = [e.data["delta"] for e in sink.events]
    assert deltas == ["Hello", " there", ""]
    assert sink.events[-1].data.get("final") is True
    assert all(json.dumps(e.data).count('"a": 1') == 0 for e in sink.events), "JSON is not streamed"
