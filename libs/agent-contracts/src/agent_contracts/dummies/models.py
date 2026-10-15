from __future__ import annotations

import json
from collections import deque
from collections.abc import AsyncIterator, Iterable
from typing import Any, Literal

from agent_contracts.dummies._util import approx_tokens, mark_dummies
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


def _example_for(schema: dict[str, Any]) -> Any:
    """Smallest value that satisfies a simple JSON schema (objects, arrays, scalars, enums)."""
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type", "object")
    if kind == "object":
        props = schema.get("properties", {})
        return {k: _example_for(v) for k, v in props.items() if k in schema.get("required", props)}
    if kind == "array":
        return [] if schema.get("minItems", 0) == 0 else [_example_for(schema.get("items", {}))]
    return {"string": "x", "integer": 0, "number": 0, "boolean": False, "null": None}.get(kind, "x")


def _usage(request: ChatRequest, text: str) -> Usage:
    prompt = sum(approx_tokens(m.content) for m in request.messages)
    return Usage(input_tokens=prompt, output_tokens=approx_tokens(text))


class EchoModelClient(ModelClient):
    """Echoes the last user message; satisfies response schemas with the smallest valid value."""

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(tool_calling=False, structured_output=True, streaming=True)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        if request.response_schema is not None:
            text = json.dumps(_example_for(request.response_schema))
        else:
            last = next((m for m in reversed(request.messages) if m.role is Role.USER), None)
            text = last.content if last else ""
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=text),
            model=request.model,
            usage=_usage(request, text),
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        response = await self.chat(request)
        for word in response.message.content.split(" "):
            yield ChatChunk(delta=word + " ")
        yield ChatChunk(finish_reason="stop", usage=response.usage)


class ScriptedModelClient(ModelClient):
    """Replays a script of assistant turns (text or tool calls), then falls back to echo.

    The basis of the "scripted fake model" used by unit tests (Step 33).
    """

    def __init__(self, script: Iterable[Message] = ()) -> None:
        self._script: deque[Message] = deque(script)
        self._fallback = EchoModelClient()
        self.requests: list[ChatRequest] = []

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(tool_calling=True, structured_output=True, streaming=True)

    def push(self, *messages: Message) -> None:
        self._script.extend(messages)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if not self._script:
            return await self._fallback.chat(request)
        message = self._script.popleft()
        finish: Literal["tool_calls", "stop"] = "tool_calls" if message.tool_calls else "stop"
        return ChatResponse(
            message=message,
            model=request.model,
            usage=_usage(request, message.content),
            finish_reason=finish,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        response = await self.chat(request)
        if response.message.content:
            yield ChatChunk(delta=response.message.content)
        calls: tuple[ToolCall, ...] = response.message.tool_calls
        yield ChatChunk(
            tool_calls=calls, finish_reason=response.finish_reason, usage=response.usage
        )


mark_dummies(globals())
