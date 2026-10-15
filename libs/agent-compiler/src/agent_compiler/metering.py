"""Metering wrappers: count model calls, tokens, AUD cost and tool calls for one agent
(pattern comparison, Step 14; per-agent cost in teams, Step 22)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from agent_contracts.interfaces import ModelClient, ToolExecutor
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    ModelCapabilities,
    RunContext,
    ToolCall,
    ToolResult,
)


@dataclass
class Meter:
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_aud: float = 0.0


class MeteredModel(ModelClient):
    def __init__(self, inner: ModelClient, meter: Meter) -> None:
        self.inner, self.meter = inner, meter

    def capabilities(self, model: str) -> ModelCapabilities:
        return self.inner.capabilities(model)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        response = await self.inner.chat(request)
        self.meter.model_calls += 1
        self.meter.input_tokens += response.usage.input_tokens
        self.meter.output_tokens += response.usage.output_tokens
        self.meter.cost_aud += response.usage.cost_aud
        return response

    def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        return self.inner.stream(request)


class MeteredTools(ToolExecutor):
    def __init__(self, inner: ToolExecutor, meter: Meter) -> None:
        self.inner, self.meter = inner, meter
        self.registry = getattr(inner, "registry", None)

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        self.meter.tool_calls += 1
        return await self.inner.execute(call, ctx)
