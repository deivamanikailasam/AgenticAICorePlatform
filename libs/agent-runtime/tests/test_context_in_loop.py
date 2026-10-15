"""The agent loop with a context window (Step 21): every call within budget, and just-in-time
loading of tools from a large tool set."""

from typing import Any

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
)
from agent_contracts.testing import ctx
from agent_runtime.agent_loop import AgentLoopExecutor
from agent_runtime.context import (
    ContextWindow,
    ExtractiveSummariser,
    RollingSummaryContextManager,
    estimate,
)
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry

TOPICS = [
    "invoice lookup",
    "vendor details",
    "currency exchange rates",
    "weather forecast",
    "calendar booking",
    "shipping tracking",
    "tax rates",
    "employee directory",
]


class Lookup(Tool):
    def __init__(self, name: str, description: str, size: int = 50) -> None:
        self._spec = ToolSpec(
            name=name,
            ref=f"local://test/{name}",
            description=description,
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            side_effect=SideEffect.READ,
            idempotent=True,
        )
        self.size = size

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def invoke(self, arguments: Any, run_ctx: Any) -> Any:
        return {"result": f"{self._spec.name}:{arguments.get('q')} " + "data " * self.size}


class SmallWindowModel(ScriptedModelClient):
    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(tool_calling=True, context_window=4000)


def call(name: str, **args: Any) -> Message:
    return Message(role=Role.ASSISTANT, tool_calls=(ToolCall(name=name, arguments=args),))


async def build(model: Any, tools: list[Tool], window: ContextWindow) -> tuple[Any, PlanNode]:
    reg = InMemoryToolRegistry()
    for t in tools:
        await reg.register(t)
    executor = AuthorisingToolExecutor(
        PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), MemoryAuditSink()
    )
    node = PlanNode(
        id="agent",
        kind=NodeKind.AGENT,
        config={"loop": "react", "tools": [t.spec.name for t in tools], "max_iterations": 40},
    )
    return AgentLoopExecutor(model, executor, reg, context=window), node


async def test_every_model_call_fits_the_window_on_a_long_tool_heavy_run() -> None:
    tool = Lookup("invoice_lookup", "Look up an invoice.", size=150)
    script = [call("invoice_lookup", q=f"INV-{i}") for i in range(25)]
    model = SmallWindowModel([*script, Message(role=Role.ASSISTANT, content="done")])
    window = ContextWindow(RollingSummaryContextManager(summariser=ExtractiveSummariser()))
    loop, node = await build(model, [tool], window)
    out = await loop(node, {"input": {"message": "Check invoices INV-0 to INV-24."}}, ctx())
    assert out["answer"] == "done"
    for request in model.requests:
        budget = window.budget(4000, request.tools)
        assert sum(estimate(m) for m in request.messages) <= budget
    last = model.requests[-1].messages
    assert last[-1].role is Role.TOOL, "the latest tool result reaches the model"
    assert "INV-24" in last[-1].content


async def test_large_tool_sets_are_loaded_just_in_time() -> None:
    tools = [
        Lookup(f"{t.replace(' ', '_')}_{i}", f"{t} service number {i}")
        for t in TOPICS
        for i in range(5)
    ]
    model = SmallWindowModel(
        [
            call("load_tools", query="currency exchange rates"),
            call("currency_exchange_rates_2", q="AUD to NZD"),
            Message(role=Role.ASSISTANT, content="1 AUD is about 1.1 NZD"),
        ]
    )
    window = ContextWindow(RollingSummaryContextManager(), jit_over=20, jit_top_k=5)
    loop, node = await build(model, tools, window)
    await loop(node, {"input": {"message": "Find the invoice for vendor 9."}}, ctx())
    first, second, third = (r.tools for r in model.requests)
    names = [t.name for t in first]
    assert len(first) == 6, "five most relevant tools plus load_tools, not all 40"
    assert "load_tools" in names
    assert any(n.startswith("invoice_lookup") for n in names)
    assert not any(n.startswith("currency") for n in names)
    assert any(t.name == "currency_exchange_rates_2" for t in second), "loaded on request"
    assert any(t.name == "currency_exchange_rates_2" for t in third), "and kept once used"
    observation = model.requests[2].messages[-1]
    assert "currency_exchange_rates_2:AUD to NZD" in observation.content


async def test_small_tool_sets_are_shown_whole_without_load_tools() -> None:
    tools = [Lookup(f"tool_{i}", f"thing {i}") for i in range(3)]
    model = SmallWindowModel([Message(role=Role.ASSISTANT, content="ok")])
    loop, node = await build(model, tools, ContextWindow(RollingSummaryContextManager()))
    await loop(node, {"input": {"message": "hi"}}, ctx())
    assert [t.name for t in model.requests[0].tools] == ["tool_0", "tool_1", "tool_2"]
