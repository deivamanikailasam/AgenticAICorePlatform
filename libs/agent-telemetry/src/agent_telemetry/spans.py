"""GenAI spans (Step 30): ``invoke_agent {agent}`` per agent run, ``chat {model}`` per model call,
``execute_tool {tool}`` per tool call, nested in one trace for a whole multi-agent run.

Spans use the global tracer provider (``agent_telemetry.setup``). Usage, model, tool and agent
attributes follow the GenAI conventions (``semconv``); prompt and response content is added only
when the content policy allows it, after redaction.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Literal

from agent_contracts.interfaces import ModelClient, ToolExecutor
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Message,
    ModelCapabilities,
    Role,
    RunContext,
    ToolCall,
    ToolResult,
    Usage,
)
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from agent_telemetry import semconv as S  # noqa: N812 - short alias for many constants
from agent_telemetry.content import messages_json, output_json, policy
from agent_telemetry.metrics import agent_metrics

SCOPE = "agent_platform"


def tracer() -> trace.Tracer:
    return trace.get_tracer(SCOPE, schema_url=f"{S.SEMCONV_REPOSITORY}/tree/{S.SEMCONV_COMMIT}")


def agent_attributes(
    name: str, version: str, ctx: RunContext, description: str | None = None
) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        S.OPERATION: S.INVOKE_AGENT,
        S.AGENT_NAME: name,
        S.AGENT_ID: f"{name}@{version}",
        S.AGENT_VERSION: version,
        S.CONVERSATION_ID: ctx.thread_id,
        S.RUN_ID: ctx.run_id,
        S.TENANT_ID: ctx.tenant_id,
        S.RISK_TIER: ctx.risk_tier.value,
    }
    if ctx.parent_run_id:
        attrs[S.PARENT_RUN_ID] = ctx.parent_run_id
    if description:
        attrs[S.AGENT_DESCRIPTION] = description
    return attrs


@contextmanager
def agent_span(
    name: str, version: str, ctx: RunContext, description: str | None = None
) -> Iterator[Span]:
    """``invoke_agent {name}`` (INTERNAL). The caller records the outcome with ``end_agent``."""
    with tracer().start_as_current_span(
        f"{S.INVOKE_AGENT} {name}",
        kind=SpanKind.INTERNAL,
        attributes=agent_attributes(name, version, ctx, description),
        record_exception=True,
    ) as span:
        yield span


def end_agent(
    span: Span, status: str, usage: Usage | None = None, stop_reason: str | None = None
) -> None:
    span.set_attribute(S.RUN_STATUS, status)
    if stop_reason:
        span.set_attribute(S.STOP_REASON, stop_reason)
    if usage is not None:
        span.set_attribute(S.INPUT_TOKENS, usage.input_tokens)
        span.set_attribute(S.OUTPUT_TOKENS, usage.output_tokens)
        span.set_attribute(S.COST_AUD, round(usage.cost_aud, 6))
    if status == "failed":
        span.set_attribute(S.ERROR_TYPE, "run_failed")
        span.set_status(Status(StatusCode.ERROR, "run failed"))


class TracingModelClient(ModelClient):
    """``chat {model}`` (CLIENT) around every model call, including retries inside it."""

    def __init__(self, inner: ModelClient, agent: str | None = None) -> None:
        self.inner = inner
        self.agent = agent

    def capabilities(self, model: str) -> ModelCapabilities:
        return self.inner.capabilities(model)

    def _start(self, request: ChatRequest) -> Span:
        attrs: dict[str, Any] = {
            S.OPERATION: S.CHAT,
            S.PROVIDER: S.PROVIDER_LITELLM,
            S.REQUEST_MODEL: request.model,
        }
        if request.max_tokens is not None:
            attrs[S.REQUEST_MAX_TOKENS] = request.max_tokens
        if request.temperature is not None:
            attrs[S.REQUEST_TEMPERATURE] = request.temperature
        if self.agent:
            attrs[S.AGENT_NAME] = self.agent
        if "run_id" in request.metadata:
            attrs[S.RUN_ID] = request.metadata["run_id"]
        return tracer().start_span(
            f"{S.CHAT} {request.model}", kind=SpanKind.CLIENT, attributes=attrs
        )

    async def _input(self, span: Span, request: ChatRequest) -> None:
        if not policy.capture:
            return
        system, history = messages_json(request.messages)
        span.set_attribute(S.SYSTEM_INSTRUCTIONS, await policy.redact(system))
        span.set_attribute(S.INPUT_MESSAGES, await policy.redact(history))

    async def _finish(self, span: Span, response: ChatResponse) -> None:
        span.set_attribute(S.RESPONSE_MODEL, response.model)
        span.set_attribute(S.FINISH_REASONS, [response.finish_reason])
        span.set_attribute(S.INPUT_TOKENS, response.usage.input_tokens)
        span.set_attribute(S.OUTPUT_TOKENS, response.usage.output_tokens)
        span.set_attribute(S.COST_AUD, round(response.usage.cost_aud, 6))
        if policy.capture:
            span.set_attribute(
                S.OUTPUT_MESSAGES,
                await policy.redact(output_json(response.message, response.finish_reason)),
            )

    def _measure(
        self, request: ChatRequest, started: float, response: ChatResponse | None, error: str | None
    ) -> None:
        usage = response.usage if response is not None else Usage()
        agent_metrics().chat_finished(
            request.model,
            response.model if response is not None else None,
            time.perf_counter() - started,
            usage.input_tokens,
            usage.output_tokens,
            self.agent,
            error,
        )

    async def chat(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        span = self._start(request)
        with trace.use_span(
            span, end_on_exit=True, record_exception=True, set_status_on_exception=True
        ):
            try:
                await self._input(span, request)
                response = await self.inner.chat(request)
            except Exception as exc:
                span.set_attribute(S.ERROR_TYPE, type(exc).__name__)
                self._measure(request, started, None, type(exc).__name__)
                raise
            await self._finish(span, response)
            self._measure(request, started, response, None)
            return response

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        started = time.perf_counter()
        span = self._start(request)
        with trace.use_span(
            span, end_on_exit=True, record_exception=True, set_status_on_exception=True
        ):
            await self._input(span, request)
            parts: list[str] = []
            calls: list[ToolCall] = []
            usage = Usage()
            finish: Literal["stop", "tool_calls", "length", "error"] = "stop"
            try:
                async for chunk in self.inner.stream(request):
                    parts.append(chunk.delta)
                    calls += chunk.tool_calls
                    usage = chunk.usage or usage
                    finish = chunk.finish_reason or finish
                    yield chunk
            except Exception as exc:
                span.set_attribute(S.ERROR_TYPE, type(exc).__name__)
                self._measure(request, started, None, type(exc).__name__)
                raise
            message = Message(role=Role.ASSISTANT, content="".join(parts), tool_calls=tuple(calls))
            response = ChatResponse(
                message=message, model=request.model, usage=usage, finish_reason=finish
            )
            await self._finish(span, response)
            self._measure(request, started, response, None)


def _tool_type(ref: str | None) -> str:
    # "function": executed by the platform; "extension": calls an external system (MCP, A2A)
    if ref and ref.split("://", 1)[0] in ("mcp", "a2a"):
        return "extension"
    return "function"


class TracingToolExecutor(ToolExecutor):
    """``execute_tool {tool}`` (INTERNAL) around every tool call, refused calls included."""

    def __init__(
        self, inner: ToolExecutor, agent: str | None = None, refs: Mapping[str, str] | None = None
    ) -> None:
        self.inner = inner
        self.agent = agent
        self.refs = dict(refs or {})
        self.registry = getattr(inner, "registry", None)  # the compiler finds tools through it

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        ref = self.refs.get(call.name)
        attrs: dict[str, Any] = {
            S.OPERATION: S.EXECUTE_TOOL,
            S.TOOL_NAME: call.name,
            S.TOOL_CALL_ID: call.id,
            S.TOOL_TYPE: _tool_type(ref),
            S.AGENT_NAME: self.agent or ctx.agent_id,
            S.CONVERSATION_ID: ctx.thread_id,
            S.RUN_ID: ctx.run_id,
        }
        if ref:
            attrs[S.TOOL_REF] = ref
        started = time.perf_counter()
        with tracer().start_as_current_span(
            f"{S.EXECUTE_TOOL} {call.name}", kind=SpanKind.INTERNAL, attributes=attrs
        ) as span:
            if policy.capture:
                span.set_attribute(
                    S.TOOL_CALL_ARGUMENTS,
                    await policy.redact(json.dumps(call.arguments, default=str)),
                )
            result = await self.inner.execute(call, ctx)
            agent_metrics().tool_finished(
                call.name,
                str(attrs[S.AGENT_NAME]),
                str(attrs[S.TOOL_TYPE]),
                time.perf_counter() - started,
                None if result.ok else (result.error_code.value if result.error_code else "failed"),
            )
            span.set_attribute(S.TOOL_OK, result.ok)
            if not result.ok:
                code = result.error_code.value if result.error_code else "failed"
                span.set_attribute(S.TOOL_ERROR_CODE, code)
                span.set_attribute(S.ERROR_TYPE, code)
                span.set_status(Status(StatusCode.ERROR, code))
            elif policy.capture:
                span.set_attribute(
                    S.TOOL_CALL_RESULT, await policy.redact(json.dumps(result.output, default=str))
                )
            return result
