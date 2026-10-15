"""Step 26 "Done when": a deliberately looping agent stops within its limits, and the run record
shows which limit tripped. Each case runs through the run API with an agent whose model never
gives a final answer."""

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from agent_api.app import build_app
from agent_api.assembly import Assembly
from agent_api.runs import RunManager
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import ModelClient
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Message,
    ModelCapabilities,
    Role,
    ToolCall,
    Usage,
)
from agent_spec import load_spec_text
from agent_spec.platform import load_platform
from agent_tools.builtin import CalculatorTool
from agent_tools.registry import InMemoryToolRegistry
from mcp.server.auth.provider import AccessToken

SPEC = """
spec_version: 1
name: looper
version: 0.1.0
owner: platform
description: An agent that never finishes on its own.
risk_tier: low
autonomy: hybrid
pattern: hybrid
runtime: { adapter: langgraph, durability: memory }
budgets: { max_steps: 50, max_wall_seconds: 60, max_tokens: 1000000, max_cost_aud: 1.0 BUDGETS }
tools:
  - ref: local://platform/calculator
graph:
  nodes:
    - { id: work, type: agent, pattern: react, tools: [calculator], max_iterations: 40 }
"""


class Looping(ModelClient):
    """Calls the calculator forever; ``expression(i)`` picks each call's argument."""

    def __init__(self, expression: Callable[[int], str], cost: float = 0.0) -> None:
        self.expression, self.cost = expression, cost
        self.calls = 0

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(tool_calling=True)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        if (request.metadata or {}).get("purpose") == "stop_summary":
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, content="It added numbers; nothing else."),
                model="default",
            )
        self.calls += 1
        call = ToolCall(name="calculator", arguments={"expression": self.expression(self.calls)})
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, tool_calls=(call,)),
            model=request.model,
            usage=Usage(input_tokens=100, output_tokens=10, cost_aud=self.cost),
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        yield ChatChunk(delta="")


class Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        return AccessToken(
            token=token,
            client_id="t",
            scopes=[],
            expires_at=None,
            claims={"preferred_username": "alice", "groups": ["/acme/ops"]},
        )


async def run_looper(
    model: Looping, budgets: str = "", tokens: str = "1000000", name: str = "looper"
) -> tuple[Any, dict[str, Any]]:
    spec = load_spec_text(
        SPEC.replace(" BUDGETS", budgets)
        .replace("max_tokens: 1000000", f"max_tokens: {tokens}")
        .replace("name: looper", f"name: {name}")
    )
    tool_calls: list[str] = []

    class Counting(CalculatorTool):
        async def invoke(self, arguments: Any, ctx: Any) -> Any:
            tool_calls.append(arguments["expression"])
            return await super().invoke(arguments, ctx)

    async def registry(s: Any) -> InMemoryToolRegistry:
        reg = InMemoryToolRegistry()
        await reg.register(Counting())
        return reg

    async def no_memory(s: Any) -> None:
        return None

    assembly = Assembly(
        load_platform(),
        RulesPolicyDecisionPoint(),
        MemoryAuditSink(),
        model_factory=lambda s: model,
        registry_factory=registry,
        memory_factory=no_memory,
    )
    manager = RunManager({spec.name: spec}, assembly)
    app = build_app(manager, Verifier())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            f"/v1/agents/{name}/runs",
            json={"input": {"task": "go"}},
            headers={"Authorization": "Bearer t"},
        )
        assert resp.status_code == 201, resp.text
        run = manager.runs[resp.json()["run_id"]]
        await manager.wait_idle(run)
    finished = next(e for e in run.events if e["type"] == "RUN_FINISHED")
    return run, {"result": finished["result"], "tool_calls": tool_calls}


async def test_a_repeating_agent_is_stopped_by_loop_detection() -> None:
    model = Looping(lambda i: "1+1")
    run, out = await run_looper(model)
    assert run.status == "stopped"
    reason = out["result"]["stop_reason"]
    assert "loop: repeated call: calculator" in reason
    assert out["tool_calls"] == ["1+1"] * 3  # stopped at the third identical call
    assert out["result"]["output"]["stop_reason"] == reason
    assert out["result"]["output"]["partial"]["transcript"], "the partial transcript is kept"


async def test_a_spending_agent_is_stopped_at_its_cost_limit() -> None:
    model = Looping(lambda i: f"{i}+1", cost=0.03)
    run, out = await run_looper(model)
    assert run.status == "stopped"
    reason = out["result"]["stop_reason"]
    assert "budget: max_cost_aud: AUD 1.0200 over the limit of 1.0" in reason
    budget = out["result"]["output"]["budget"]
    assert budget["tripped"] == "max_cost_aud"
    assert 1.0 < budget["cost_aud"] <= 1.0 + 0.03  # stopped by the call that crossed it
    assert model.calls == 34


@pytest.mark.parametrize(
    ("budgets", "tokens", "limit"),
    [
        (", max_tool_calls: 5", "1000000", "max_tool_calls"),
        (", max_calls_per_tool: { calculator: 4 }", "1000000", "max_calls_per_tool"),
        ("", "500", "max_tokens"),
    ],
)
async def test_each_limit_is_named_in_the_run_record(budgets: str, tokens: str, limit: str) -> None:
    run, out = await run_looper(Looping(lambda i: f"{i}*2"), budgets, tokens)
    assert run.status == "stopped"
    assert out["result"]["output"]["budget"]["tripped"] == limit
    assert f"budget: {limit}" in out["result"]["stop_reason"]


async def test_stop_and_summarise_adds_a_summary_of_the_partial_result() -> None:
    model = Looping(lambda i: "2+2")
    run, out = await run_looper(model, ", on_exceed: summarise")
    assert run.status == "stopped"
    assert out["result"]["output"]["summary"] == "It added numbers; nothing else."
