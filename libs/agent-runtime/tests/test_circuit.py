"""Circuit breakers on failing tools and models (Step 26)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_contracts.interfaces import ModelClient, StopRun, ToolExecutor
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Message,
    ModelCapabilities,
    Role,
    RunContext,
    ToolCall,
    ToolErrorCode,
    ToolResult,
)
from agent_runtime.circuit import (
    CircuitBreaker,
    CircuitBreakingModelClient,
    CircuitBreakingToolExecutor,
)


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


class Flaky(ToolExecutor):
    def __init__(self) -> None:
        self.calls = 0
        self.code: ToolErrorCode | None = ToolErrorCode.TIMEOUT

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        self.calls += 1
        if self.code is None:
            return ToolResult(call_id=call.id, tool=call.name, ok=True, output=1)
        return ToolResult(
            call_id=call.id, tool=call.name, ok=False, error_code=self.code, error="x"
        )


def ctx() -> RunContext:
    return RunContext(tenant_id="t", user_id="u", agent_id="a")


async def test_a_failing_tool_opens_then_half_opens_then_closes() -> None:
    clock, inner = Clock(), Flaky()
    tools = CircuitBreakingToolExecutor(inner, CircuitBreaker(failures=3, cooldown=10, clock=clock))
    call = ToolCall(name="erp", arguments={})
    for _ in range(3):
        await tools.execute(call, ctx())
    refused = await tools.execute(call, ctx())
    assert refused.error_code is ToolErrorCode.UNAVAILABLE
    assert "circuit open" in (refused.error or "")
    assert inner.calls == 3, "an open circuit never reaches the broken tool"
    clock.now = 10
    await tools.execute(call, ctx())  # the trial fails: open again
    assert inner.calls == 4
    assert (await tools.execute(call, ctx())).error_code is ToolErrorCode.UNAVAILABLE
    clock.now = 20
    inner.code = None
    assert (await tools.execute(call, ctx())).ok
    assert (await tools.execute(call, ctx())).ok
    assert inner.calls == 6


async def test_denials_and_bad_arguments_do_not_count() -> None:
    inner = Flaky()
    inner.code = ToolErrorCode.DENIED
    tools = CircuitBreakingToolExecutor(inner, CircuitBreaker(failures=2))
    for _ in range(5):
        r = await tools.execute(ToolCall(name="pay", arguments={}), ctx())
        assert r.error_code is ToolErrorCode.DENIED
    assert inner.calls == 5


class Down(ModelClient):
    def __init__(self) -> None:
        self.calls = 0
        self.up = False

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        if not self.up:
            raise ConnectionError("gateway 503")
        return ChatResponse(message=Message(role=Role.ASSISTANT, content="ok"), model=request.model)

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        yield ChatChunk(delta="")


async def test_a_failing_model_stops_the_run_with_the_reason() -> None:
    clock, inner = Clock(), Down()
    model = CircuitBreakingModelClient(inner, CircuitBreaker(failures=2, cooldown=5, clock=clock))
    req = ChatRequest(model="planner", messages=(Message(role=Role.USER, content="hi"),))
    for _ in range(2):
        with pytest.raises(ConnectionError):
            await model.chat(req)
    with pytest.raises(StopRun, match="circuit open: model planner"):
        await model.chat(req)
    assert inner.calls == 2
    other: Any = req.model_copy(update={"model": "default"})
    with pytest.raises(ConnectionError):  # each role has its own circuit
        await model.chat(other)
    clock.now = 5
    inner.up = True
    assert (await model.chat(req)).message.content == "ok"
