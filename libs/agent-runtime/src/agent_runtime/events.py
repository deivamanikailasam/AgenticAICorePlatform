"""Run events from inside a run (Step 17), on every runtime adapter and across resumes.

A run's events are collected by a sink bound to the run's task (a context variable), so nothing
needs threading through the adapters:

* ``EventingNodeRunner`` — ``node_started`` / ``node_finished`` (outcome, error) and a
  ``state_delta`` with the node's value, for every node, including after a resume;
* ``EventingToolExecutor`` — ``tool_call_started`` (id, tool, arguments) and
  ``tool_call_finished`` (ok, output or error) around every tool call;
* ``StreamingModelClient`` — ``text_delta`` events: token by token from the model client's
  stream for plain-text replies, or the whole text for replies that also call tools; structured
  (JSON) replies are not streamed as text.

Without a bound sink every wrapper is a pass-through.
"""

from __future__ import annotations

import contextvars
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from agent_contracts.interfaces import ModelClient, ToolExecutor
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Message,
    ModelCapabilities,
    PlanNode,
    Role,
    RunContext,
    RunEvent,
    RunEventType,
    ToolCall,
    ToolResult,
    Usage,
    new_id,
)

from agent_runtime.nodes import NodeOutcome, NodeRunner

Sink = Callable[[RunEvent], Awaitable[None]]
current_sink: contextvars.ContextVar[Sink | None] = contextvars.ContextVar(
    "run_event_sink", default=None
)


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


async def emit(type_: RunEventType, run_id: str, node_id: str | None = None, **data: Any) -> None:
    sink = current_sink.get()
    if sink is not None:
        await sink(RunEvent(type=type_, run_id=run_id, node_id=node_id, data=json_safe(data)))


class EventingNodeRunner(NodeRunner):
    async def run(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> NodeOutcome:
        await emit(RunEventType.NODE_STARTED, ctx.run_id, node.id, kind=node.kind.value)
        result = await super().run(node, values, ctx)
        await emit(
            RunEventType.NODE_FINISHED,
            ctx.run_id,
            node.id,
            outcome=result.outcome,
            error=result.error,
        )
        await emit(RunEventType.STATE_DELTA, ctx.run_id, node.id, values={node.id: result.value})
        return result


class EventingToolExecutor(ToolExecutor):
    def __init__(self, inner: ToolExecutor) -> None:
        self.inner = inner
        self.registry = getattr(inner, "registry", None)

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        await emit(
            RunEventType.TOOL_CALL_STARTED,
            ctx.run_id,
            tool_call_id=call.id,
            tool=call.name,
            arguments=call.arguments,
        )
        result = await self.inner.execute(call, ctx)
        await emit(
            RunEventType.TOOL_CALL_FINISHED,
            ctx.run_id,
            tool_call_id=call.id,
            tool=call.name,
            ok=result.ok,
            output=result.output if result.ok else None,
            error=result.error,
            error_code=result.error_code.value if result.error_code else None,
        )
        return result


class StreamingModelClient(ModelClient):
    def __init__(self, inner: ModelClient) -> None:
        self.inner = inner

    def capabilities(self, model: str) -> ModelCapabilities:
        return self.inner.capabilities(model)

    def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        return self.inner.stream(request)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        run_id = str(request.metadata.get("run_id", "-"))
        node_id = request.metadata.get("node_id")
        message_id = new_id()
        if current_sink.get() is None or request.response_schema is not None:
            return await self.inner.chat(request)
        if request.tools or not self.capabilities(request.model).streaming:
            response = await self.inner.chat(request)
            if response.message.content:
                await emit(
                    RunEventType.TEXT_DELTA,
                    run_id,
                    node_id,
                    message_id=message_id,
                    delta=response.message.content,
                    final=True,
                )
            return response
        parts: list[str] = []
        usage = Usage()
        finish = "stop"
        async for chunk in self.inner.stream(request):
            if chunk.delta:
                parts.append(chunk.delta)
                await emit(
                    RunEventType.TEXT_DELTA,
                    run_id,
                    node_id,
                    message_id=message_id,
                    delta=chunk.delta,
                )
            if chunk.usage is not None:
                usage = chunk.usage
            if chunk.finish_reason:
                finish = chunk.finish_reason
        await emit(
            RunEventType.TEXT_DELTA, run_id, node_id, message_id=message_id, delta="", final=True
        )
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content="".join(parts)),
            model=request.model,
            usage=usage,
            finish_reason=finish,  # type: ignore[arg-type]
        )
