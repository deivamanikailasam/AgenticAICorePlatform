"""Step 13: the single tool-calling agent from ``pattern: react``, on both runtime adapters.

Done when: the agent solves the golden tool tasks; malformed tool calls are repaired or fail
cleanly (test_agent_loop.py); the loop stops at max_steps.
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from agent_compiler.compiler import compile_spec
from agent_compiler.run import adapter_for, run
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.models import (
    ChatRequest,
    ChatResponse,
    Message,
    ModelCapabilities,
    Role,
    RunStatus,
    SideEffect,
    ToolCall,
)
from agent_contracts.testing import ctx
from agent_spec import load_spec_text
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.builtin import CalculatorTool, WordCountTool
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry
from opentelemetry.sdk.trace import TracerProvider

ROOT = Path(__file__).resolve().parents[3]
CASES = [
    json.loads(line)
    for line in (ROOT / "evals/golden/react-tools/cases.jsonl").read_text().splitlines()
    if line.strip()
]
ADAPTERS = ["langgraph", "agent_framework"]
PROVIDER = TracerProvider()


def react_spec(
    adapter: str, max_steps: int = 8, extra_tools: str = "", engine: str = "native"
) -> str:
    return f"""
spec_version: 1
name: tool-agent
version: 0.1.0
owner: agent-platform
description: Answers questions with the calculator and word counter.
risk_tier: low
autonomy: agent
pattern: react
models: {{ default: local-qwen-fast }}
runtime: {{ adapter: {adapter}, durability: memory }}
components: {{ agent_loop: {engine} }}
budgets: {{ max_steps: {max_steps}, max_wall_seconds: 60, max_tokens: 20000, max_cost_aud: 1.0 }}
tools:
  - ref: local://platform/calculator
  - ref: local://platform/word_count
{extra_tools}
"""


async def tools() -> AuthorisingToolExecutor:
    reg = InMemoryToolRegistry()
    for t in (CalculatorTool(), WordCountTool()):
        await reg.register(t)
    return AuthorisingToolExecutor(
        PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), MemoryAuditSink()
    )


class GoldenStandIn(ScriptedModelClient):
    """Deterministic stand-in for a model: issues each golden case's expected call, then answers
    from the observation. It tests the plumbing (loop, tools, policy, answer), not model skill."""

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        last = request.messages[-1]
        if last.role is Role.TOOL:
            reply = Message(role=Role.ASSISTANT, content=f"The answer is {last.content}")
        else:
            case = next(c for c in CASES if c["question"] in last.content)
            reply = Message(
                role=Role.ASSISTANT,
                tool_calls=(ToolCall(name=case["tool"], arguments=case["arguments"]),),
            )
        return ChatResponse(message=reply, model=request.model)


@pytest.mark.parametrize("engine", ["native", "openai_agents", "pydantic_ai"])
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_golden_tool_tasks_mechanics(adapter: str, engine: str) -> None:
    spec = load_spec_text(react_spec(adapter, engine=engine))
    solved = []
    for case in CASES:
        result, _ = await run(
            spec,
            {"question": case["question"]},
            ctx(),
            PROVIDER,
            model=GoldenStandIn(),
            tools=await tools(),
        )
        assert result.status is RunStatus.SUCCEEDED, (case["id"], result.state.error)
        if case["expect"] in str(result.output["answer"]):
            solved.append(case["id"])
    assert solved == [c["id"] for c in CASES]


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_run_stops_at_max_steps(adapter: str) -> None:
    looping = ScriptedModelClient(
        [
            Message(
                role=Role.ASSISTANT,
                tool_calls=(ToolCall(name="calculator", arguments={"expression": "1+1"}),),
            )
        ]
        * 10
    )
    result, _ = await run(
        load_spec_text(react_spec(adapter, max_steps=3)),
        {"question": "loop forever"},
        ctx(),
        PROVIDER,
        model=looping,
        tools=await tools(),
    )
    assert result.status is RunStatus.STOPPED
    assert "max_steps (3) reached" in (result.state.stop_reason or "")
    assert len(looping.requests) == 3


class Recorder(CalculatorTool):
    """A write tool for the approval test (reuses the calculator's schema)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def spec(self) -> Any:
        return super().spec.model_copy(
            update={
                "name": "record",
                "ref": "local://test/record",
                "side_effect": SideEffect.WRITE,
                "idempotent": False,
            }
        )

    async def invoke(self, arguments: Any, run_ctx: Any) -> Any:
        self.calls.append(dict(arguments))
        return {"recorded": True}


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_tool_needing_approval_interrupts_and_resumes(adapter: str) -> None:
    executor = await tools()
    recorder = Recorder()
    await executor.registry.register(recorder)
    text = react_spec(
        adapter,
        extra_tools='  - ref: local://test/record\n    approval: { required: true, approvers: "group:finance-approvers" }',
    )
    spec = load_spec_text(text)
    plan = compile_spec(spec)
    model = ScriptedModelClient(
        [
            Message(
                role=Role.ASSISTANT,
                tool_calls=(ToolCall(name="record", arguments={"expression": "5"}),),
            ),
            Message(role=Role.ASSISTANT, content="recorded it"),
        ]
    )
    adapter_ = adapter_for(spec, plan, model=model, tools=executor)
    handle = await adapter_.build(plan)
    c = ctx()
    first = await adapter_.invoke(handle, {"question": "record 5"}, c)
    assert first.status is RunStatus.INTERRUPTED
    assert first.state.next_nodes == ("approval",)
    assert recorder.calls == []
    decision = {"approved": True, "by": "j.smith", "groups": ["/acme/finance-approvers"]}
    done = await adapter_.resume(handle, c.run_id, decision, c)
    assert done.status is RunStatus.SUCCEEDED, done.state.error
    assert done.output["answer"] == "recorded it"
    assert recorder.calls == [{"expression": "5"}]


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_approval_from_someone_outside_the_spec_group_does_not_count(adapter: str) -> None:
    executor = await tools()
    recorder = Recorder()
    await executor.registry.register(recorder)
    text = react_spec(
        adapter,
        extra_tools='  - ref: local://test/record\n    approval: { required: true, approvers: "group:finance-approvers" }',
    )
    spec = load_spec_text(text)
    model = ScriptedModelClient(
        [
            Message(
                role=Role.ASSISTANT,
                tool_calls=(ToolCall(name="record", arguments={"expression": "5"}),),
            ),
            Message(role=Role.ASSISTANT, content="could not record"),
        ]
    )
    adapter_ = adapter_for(spec, compile_spec(spec), model=model, tools=executor)
    handle = await adapter_.build(compile_spec(spec))
    c = ctx()
    await adapter_.invoke(handle, {"question": "record 5"}, c)
    decision = {"approved": True, "by": "mallory", "groups": ["/acme/sales"]}
    done = await adapter_.resume(handle, c.run_id, decision, c)
    assert done.status is RunStatus.SUCCEEDED, done.state.error
    assert recorder.calls == []
    assert "not in group:finance-approvers" in model.requests[-1].messages[-1].content


class TextOnlyStandIn(GoldenStandIn):
    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(tool_calling=False)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        last = request.messages[-1]
        if "Observation from" in last.content:  # delimited as untrusted data (Step 21)
            text = f"Final Answer: {last.content}"
        else:
            case = next(c for c in CASES if c["question"] in last.content)
            text = f"Thought: use a tool.\nAction: {case['tool']}\nAction Input: {json.dumps(case['arguments'])}"
        return ChatResponse(message=Message(role=Role.ASSISTANT, content=text), model=request.model)


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_golden_tasks_through_the_text_react_fallback(adapter: str) -> None:
    spec = load_spec_text(react_spec(adapter))
    for case in CASES:
        model = TextOnlyStandIn()
        result, _ = await run(
            spec, {"question": case["question"]}, ctx(), PROVIDER, model=model, tools=await tools()
        )
        assert result.status is RunStatus.SUCCEEDED, (case["id"], result.state.error)
        assert case["expect"] in str(result.output["answer"])
        assert model.requests[0].tools == ()


@pytest.mark.mac
async def test_golden_tool_tasks_with_the_real_local_model() -> None:
    """VERIFY-ON-MAC: Project 0's local model through the gateway. Prints solved/total."""
    from agent_runtime.models.gateway import GatewayModelClient

    if not os.environ.get("P0_LITELLM_URL"):
        pytest.skip("needs Project 0's gateway")
    spec = load_spec_text(react_spec("langgraph"))
    solved = 0
    for case in CASES:
        result, _ = await run(
            spec,
            {"question": case["question"]},
            ctx(),
            PROVIDER,
            model=GatewayModelClient(spec_models=spec.models),
            tools=await tools(),
        )
        ok = result.status is RunStatus.SUCCEEDED and case["expect"] in str(result.output)
        solved += ok
        print(f"{case['id']}: {'solved' if ok else 'missed'}")
    print(f"golden react-tools: {solved}/{len(CASES)} solved")
    assert solved > 0
