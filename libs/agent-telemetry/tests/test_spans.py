"""Step 30: GenAI spans follow the conventions; content is captured only for Langfuse, redacted,
and never reaches a general exporter."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.tools import EchoTool, InMemoryToolRegistry, LocalToolExecutor
from agent_contracts.models import (
    ChatRequest,
    ChatResponse,
    Message,
    Role,
    RunContext,
    ToolCall,
    Usage,
)
from agent_contracts.testing import TelemetryEmitterContract
from agent_telemetry import content
from agent_telemetry import semconv as S  # noqa: N812
from agent_telemetry.emitter import OtelTelemetryEmitter
from agent_telemetry.export import ContentFreeExporter, LangfuseTarget, strip_content
from agent_telemetry.spans import TracingModelClient, TracingToolExecutor, agent_span, end_agent
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

CTX = RunContext(tenant_id="acme", user_id="alice", agent_id="triage", thread_id="th-1")


@pytest.fixture
def no_capture() -> Iterator[None]:
    yield
    content.configure(False, None)


class Fixed(ScriptedModelClient):
    """Answers every request with one prepared response."""

    def __init__(self, response: ChatResponse) -> None:
        super().__init__()
        self.response = response

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return self.response


def by_name(exporter: InMemorySpanExporter) -> dict[str, Any]:
    return {s.name: s for s in exporter.get_finished_spans()}


async def test_agent_chat_and_tool_spans_nest_with_genai_attributes(
    span_exporter: InMemorySpanExporter, no_capture: None
) -> None:
    model = TracingModelClient(
        Fixed(
            ChatResponse(
                message=Message(role=Role.ASSISTANT, content="done"),
                model="local-qwen-fast",
                usage=Usage(input_tokens=12, output_tokens=3, cost_aud=0.001),
            )
        ),
        agent="triage",
    )
    reg = InMemoryToolRegistry()
    await reg.register(EchoTool())
    tools = TracingToolExecutor(
        LocalToolExecutor(reg), agent="triage", refs={"echo": "local://x/echo"}
    )
    with agent_span("triage", "1.2.0", CTX, "Sorts invoices") as span:
        await model.chat(
            ChatRequest(model="executor", messages=(Message(role=Role.USER, content="hi"),))
        )
        await tools.execute(ToolCall(id="c1", name="echo", arguments={"text": "x"}), CTX)
        end_agent(span, "succeeded", Usage(input_tokens=12, output_tokens=3))
    spans = by_name(span_exporter)
    root, chat, tool = (
        spans["invoke_agent triage"],
        spans["chat executor"],
        spans["execute_tool echo"],
    )
    assert root.kind is SpanKind.INTERNAL
    assert chat.kind is SpanKind.CLIENT
    assert tool.kind is SpanKind.INTERNAL
    assert chat.parent.span_id == root.context.span_id
    assert tool.parent.span_id == root.context.span_id
    assert {chat.context.trace_id, tool.context.trace_id} == {root.context.trace_id}
    assert root.attributes[S.OPERATION] == "invoke_agent"
    assert root.attributes[S.AGENT_ID] == "triage@1.2.0"
    assert root.attributes[S.CONVERSATION_ID] == "th-1"
    assert root.attributes[S.RUN_STATUS] == "succeeded"
    assert chat.attributes[S.PROVIDER] == "litellm"
    assert chat.attributes[S.REQUEST_MODEL] == "executor"
    assert chat.attributes[S.RESPONSE_MODEL] == "local-qwen-fast"
    assert chat.attributes[S.INPUT_TOKENS] == 12
    assert chat.attributes[S.OUTPUT_TOKENS] == 3
    assert tuple(chat.attributes[S.FINISH_REASONS]) == ("stop",)
    assert tool.attributes[S.TOOL_NAME] == "echo"
    assert tool.attributes[S.TOOL_CALL_ID] == "c1"
    assert tool.attributes[S.TOOL_TYPE] == "function"
    assert tool.attributes[S.TOOL_OK] is True
    for span in spans.values():  # capture is off: no content anywhere
        assert not set(span.attributes) & S.CONTENT_ATTRIBUTES


async def test_failed_tool_calls_carry_error_type(span_exporter: InMemorySpanExporter) -> None:
    tools = TracingToolExecutor(LocalToolExecutor(InMemoryToolRegistry()))
    result = await tools.execute(ToolCall(name="missing"), CTX)
    assert not result.ok
    span = by_name(span_exporter)["execute_tool missing"]
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes[S.ERROR_TYPE] == span.attributes[S.TOOL_ERROR_CODE]


async def test_model_errors_are_recorded(span_exporter: InMemorySpanExporter) -> None:
    class Down(ScriptedModelClient):
        async def chat(self, request: ChatRequest) -> ChatResponse:
            raise TimeoutError("gateway")

    with pytest.raises(TimeoutError):
        await TracingModelClient(Down([])).chat(ChatRequest(model="m", messages=()))
    span = by_name(span_exporter)["chat m"]
    assert span.attributes[S.ERROR_TYPE] == "TimeoutError"
    assert span.status.status_code is StatusCode.ERROR


async def test_streamed_calls_get_one_span_with_usage(span_exporter: InMemorySpanExporter) -> None:
    from agent_contracts.models import ChatChunk

    class Streaming(ScriptedModelClient):
        async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
            yield ChatChunk(delta="hel")
            yield ChatChunk(
                delta="lo", finish_reason="stop", usage=Usage(input_tokens=5, output_tokens=2)
            )

    chunks = [
        c
        async for c in TracingModelClient(Streaming([])).stream(ChatRequest(model="m", messages=()))
    ]
    assert len(chunks) == 2
    span = by_name(span_exporter)["chat m"]
    assert span.attributes[S.OUTPUT_TOKENS] == 2


async def test_content_is_redacted_and_only_for_the_langfuse_exporter(
    span_exporter: InMemorySpanExporter, no_capture: None
) -> None:
    async def redact(text: str) -> str:
        return text.replace("alice@example.com", "<EMAIL_ADDRESS>")

    content.configure(True, redact)
    model = TracingModelClient(
        Fixed(
            ChatResponse(
                message=Message(role=Role.ASSISTANT, content="Mailed alice@example.com"), model="m"
            )
        )
    )
    request = ChatRequest(
        model="m",
        messages=(
            Message(role=Role.SYSTEM, content="Be brief."),
            Message(role=Role.USER, content="Email alice@example.com the invoice"),
        ),
    )
    await model.chat(request)
    span = by_name(span_exporter)["chat m"]
    captured = " ".join(str(span.attributes[k]) for k in (S.INPUT_MESSAGES, S.OUTPUT_MESSAGES))
    assert "<EMAIL_ADDRESS>" in captured
    assert "alice@example.com" not in captured
    assert "Be brief." in str(span.attributes[S.SYSTEM_INSTRUCTIONS])

    # the general exporter (Tempo) never receives content
    general = InMemorySpanExporter()
    ContentFreeExporter(general).export([span])
    (exported,) = general.get_finished_spans()
    assert not set(exported.attributes) & S.CONTENT_ATTRIBUTES
    assert exported.attributes[S.REQUEST_MODEL] == "m"
    assert not set(strip_content(span).attributes) & S.CONTENT_ATTRIBUTES


async def test_content_is_withheld_when_redaction_is_unavailable(
    span_exporter: InMemorySpanExporter, no_capture: None
) -> None:
    async def broken(text: str) -> str:
        raise ConnectionError("presidio down")

    content.configure(True, broken)
    tools = TracingToolExecutor(LocalToolExecutor(InMemoryToolRegistry()))
    await tools.execute(ToolCall(name="t", arguments={"card": "4111 1111 1111 1111"}), CTX)
    span = by_name(span_exporter)["execute_tool t"]
    assert span.attributes[S.TOOL_CALL_ARGUMENTS] == content.WITHHELD
    content.configure(True, None)  # capture without a redactor is refused too
    assert await content.policy.redact("anything") == content.WITHHELD


def test_capture_is_on_only_with_a_langfuse_exporter(no_capture: None) -> None:
    from agent_telemetry.export import build_provider

    build_provider("t", InMemorySpanExporter(), None, batch=False)
    assert content.policy.capture is False
    build_provider("t", InMemorySpanExporter(), InMemorySpanExporter(), batch=False)
    assert content.policy.capture is True


def test_langfuse_target_uses_its_otlp_endpoint_and_basic_auth() -> None:
    target = LangfuseTarget("http://langfuse:3000/", "pk-lf-1", "sk-lf-2")
    assert target.endpoint == "http://langfuse:3000/api/public/otel/v1/traces"
    assert target.headers["Authorization"] == "Basic cGstbGYtMTpzay1sZi0y"


class TestOtelTelemetryEmitter(TelemetryEmitterContract):
    def make(self) -> OtelTelemetryEmitter:
        return OtelTelemetryEmitter()
