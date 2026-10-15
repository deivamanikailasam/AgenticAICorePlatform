"""Step 15: prompt chains with validation gates, and the router pattern with mixed handlers,
from agent.yaml on both runtime adapters."""

import json
from typing import Any

import pytest
from agent_compiler.compiler import compile_spec
from agent_compiler.run import run
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.models import Message, Role, RunStatus, ToolCall
from agent_contracts.testing import ctx
from agent_spec import load_spec_text
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.builtin import CalculatorTool
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry
from opentelemetry.sdk.trace import TracerProvider

ADAPTERS = ["langgraph", "agent_framework"]
PROVIDER = TracerProvider()


def say(obj: Any) -> Message:
    return Message(role=Role.ASSISTANT, content=obj if isinstance(obj, str) else json.dumps(obj))


def spec(
    adapter: str, pattern: str, graph: str, autonomy: str = "workflow", tools: str = ""
) -> str:
    return f"""
spec_version: 1
name: {pattern.replace("_", "-")}-test
version: 0.1.0
owner: agent-platform
description: Step 15 pattern test.
risk_tier: low
autonomy: {autonomy}
pattern: {pattern}
runtime: {{ adapter: {adapter}, durability: memory }}
budgets: {{ max_steps: 20, max_wall_seconds: 30, max_tokens: 10000, max_cost_aud: 1.0 }}
{tools}
graph:
{graph}
"""


CHAIN = """
  nodes:
    - id: extract
      type: llm
      config:
        template: "Extract the amount and currency from: $input.text"
        output_schema: { type: object, properties: { amount: { type: number }, currency: { type: string } }, required: [amount, currency] }
        gate:
          checks: [ { path: currency, one_of: [AUD, USD] } ]
          max_retries: 1
    - id: draft
      type: llm
      config:
        template: "Write one sentence confirming ${extract.amount} ${extract.currency}."
    - { id: needs_human, type: function, fn: identity }
  edges: ["extract -> draft: on_ok", "extract -> needs_human: on_error"]
"""


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_prompt_chain_gate_repairs_then_passes_the_value_on(adapter: str) -> None:
    model = ScriptedModelClient(
        [
            say({"amount": 480, "currency": "dollars"}),  # fails the gate
            say({"amount": 480, "currency": "AUD"}),
            say("Confirmed: AUD 480."),
        ]
    )
    result, _ = await run(
        load_spec_text(spec(adapter, "prompt_chain", CHAIN)),
        {"text": "pay four hundred and eighty Australian dollars"},
        ctx(),
        PROVIDER,
        model=model,
    )
    assert result.status is RunStatus.SUCCEEDED, result.state.error
    assert result.state.values["draft"] == "Confirmed: AUD 480."
    assert "480 AUD" in model.requests[2].messages[-1].content
    assert "needs_human" not in result.state.values


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_prompt_chain_gate_exhausted_takes_the_on_error_edge(adapter: str) -> None:
    model = ScriptedModelClient([say({"amount": 1, "currency": "yen"})] * 2)
    result, _ = await run(
        load_spec_text(spec(adapter, "prompt_chain", CHAIN)),
        {"text": "?"},
        ctx(),
        PROVIDER,
        model=model,
    )
    assert result.status is RunStatus.SUCCEEDED, result.state.error
    assert "needs_human" in result.state.values
    assert "draft" not in result.state.values
    assert len(model.requests) == 2


def test_prompt_chain_rejects_ungated_steps_and_branches() -> None:
    ungated = CHAIN.replace("gate:", "x_gate:")
    with pytest.raises(ValueError, match="needs a validation gate"):
        compile_spec(load_spec_text(spec("langgraph", "prompt_chain", ungated)))
    branching = CHAIN.replace('"extract -> needs_human: on_error"', '"extract -> needs_human"')
    with pytest.raises(ValueError, match="straight line"):
        compile_spec(load_spec_text(spec("langgraph", "prompt_chain", branching)))
    leaky = CHAIN.replace('"extract -> draft: on_ok"', '"extract -> draft"')
    with pytest.raises(ValueError, match="must be 'on_ok'"):
        compile_spec(load_spec_text(spec("langgraph", "prompt_chain", leaky)))


ROUTED = """
  entry: triage
  nodes:
    - id: triage
      type: router
      routes: [maths, chat]
      config: { router: rules, template: "$input.question", default: chat, rules: [{ route: maths, any: ["\\\\d"] }] }
    - { id: maths, type: agent, pattern: react, tools: [calculator], max_iterations: 4 }
    - { id: chat, type: llm, config: { template: "Reply briefly to: $input.question" } }
  edges: ["triage -> maths: maths", "triage -> chat: chat"]
"""
TOOLS = "tools:\n  - ref: local://platform/calculator"


async def calculator() -> AuthorisingToolExecutor:
    reg = InMemoryToolRegistry()
    await reg.register(CalculatorTool())
    return AuthorisingToolExecutor(
        PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), MemoryAuditSink()
    )


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_router_pattern_sends_requests_to_an_agent_or_an_llm_step(adapter: str) -> None:
    text = load_spec_text(spec(adapter, "router", ROUTED, "hybrid", TOOLS))
    maths_model = ScriptedModelClient(
        [
            Message(
                role=Role.ASSISTANT,
                tool_calls=(ToolCall(name="calculator", arguments={"expression": "6 * 7"}),),
            ),
            say("42"),
        ]
    )
    maths, _ = await run(
        text,
        {"question": "what is 6 * 7"},
        ctx(),
        PROVIDER,
        model=maths_model,
        tools=await calculator(),
    )
    assert maths.status is RunStatus.SUCCEEDED, maths.state.error
    assert maths.state.values["triage"]["route"] == "maths"
    assert "42" in json.dumps(maths.state.values)

    chat_model = ScriptedModelClient([say("Hello!")])
    chat, _ = await run(
        text, {"question": "hi there"}, ctx(), PROVIDER, model=chat_model, tools=await calculator()
    )
    assert chat.state.values["triage"]["route"] == "chat"
    assert chat.state.values["chat"] == "Hello!"


def test_router_pattern_needs_a_router_entry_with_every_route_handled() -> None:
    missing = ROUTED.replace("routes: [maths, chat]", "routes: [maths, chat, legal]")
    with pytest.raises(ValueError, match=r"no edge for routes \['legal'\]"):
        compile_spec(load_spec_text(spec("langgraph", "router", missing, "hybrid", TOOLS)))
