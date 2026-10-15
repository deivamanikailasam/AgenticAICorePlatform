import json
from datetime import date
from typing import Any

import httpx
import pytest
import respx
from agent_contracts.models import ChatRequest, Message, ModelCapabilities, Role, ToolDefinition
from agent_contracts.testing import ModelClientContract
from agent_runtime.models import (
    CapabilityError,
    GatewayError,
    GatewayModelClient,
    StructuredOutputError,
)
from agent_spec.platform import Currency, Gateway, Models, PlatformConfig

URL = "http://gw.test/v1/chat/completions"
CAPS = ModelCapabilities(tool_calling=True, structured_output=True, streaming=True)


def platform(**caps: ModelCapabilities) -> PlatformConfig:
    return PlatformConfig(
        gateway=Gateway(url="http://gw.test", max_retries=2),
        models=Models(
            roles={
                "default": "fast",
                "planner": "quality",
                "executor": "fast",
                "router": "fast",
                "judge": "quality",
                "summariser": "fast",
            },
            capabilities={"fast": CAPS, "quality": CAPS, "test": CAPS, **caps},
        ),
        currency=Currency(usd_to_aud=1.5, source="test", as_of=date(2026, 10, 1)),
    )


def client(**kw: Any) -> GatewayModelClient:
    return GatewayModelClient(
        platform(**kw.pop("caps", {})),
        keys={"default": "sk-default", "planner": "sk-planner"},
        backoff_seconds=0,
        **kw,
    )


def completion(
    content: str = "hi", tool_calls: list[dict[str, Any]] | None = None, finish: str = "stop"
) -> dict[str, Any]:
    return {
        "model": "fast",
        "choices": [
            {
                "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }


def req(model: str = "default", **kw: Any) -> ChatRequest:
    return ChatRequest(model=model, messages=(Message(role=Role.USER, content="say hi"),), **kw)


SSE = (
    'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
    "data: [DONE]\n\n"
)


class TestGatewayModelClientContract(ModelClientContract):
    @pytest.fixture(autouse=True)
    def _gateway(self) -> Any:
        with respx.mock(assert_all_called=False) as mock:

            def answer(request: httpx.Request) -> httpx.Response:
                body = json.loads(request.content)
                if body.get("stream"):
                    return httpx.Response(
                        200, text=SSE, headers={"content-type": "text/event-stream"}
                    )
                if "response_format" in body:
                    return httpx.Response(200, json=completion('{"n": 7}'))
                return httpx.Response(200, json=completion())

            mock.post(URL).mock(side_effect=answer)
            yield

    def make(self) -> GatewayModelClient:
        return client()


@respx.mock
async def test_roles_resolve_and_use_their_own_key() -> None:
    route = respx.post(URL).respond(200, json=completion())
    c = GatewayModelClient(
        platform(), spec_models={"planner": "test"}, keys={"default": "sk-d", "planner": "sk-p"}
    )
    assert c.resolve("planner") == ("planner", "test")
    assert c.resolve("judge") == ("judge", "quality")
    assert c.resolve("some-alias") == ("default", "some-alias")
    await c.chat(req("planner"))
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer sk-p"
    assert json.loads(sent.content)["model"] == "test"


@respx.mock
async def test_tool_calls_round_trip() -> None:
    route = respx.post(URL).respond(
        200,
        json=completion(
            "",
            [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "add", "arguments": '{"a":1,"b":2}'},
                }
            ],
            "tool_calls",
        ),
    )
    tool = ToolDefinition(
        name="add", parameters={"type": "object", "properties": {"a": {"type": "number"}}}
    )
    resp = await client().chat(req(tools=(tool,)))
    assert resp.finish_reason == "tool_calls"
    assert resp.message.tool_calls[0].name == "add"
    assert resp.message.tool_calls[0].arguments == {"a": 1, "b": 2}
    body = json.loads(route.calls[0].request.content)
    assert body["tools"][0]["function"]["name"] == "add"


async def test_capability_check_refuses_tools_on_model_without_tool_calling() -> None:
    c = client(caps={"tiny": ModelCapabilities(tool_calling=False)})
    with pytest.raises(CapabilityError, match="does not support tool calling"):
        await c.chat(req("tiny", tools=(ToolDefinition(name="x"),)))


@respx.mock
async def test_structured_output_repairs_once() -> None:
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(200, json=completion("not json")),
            httpx.Response(200, json=completion('{"n": 1}')),
        ]
    )
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    resp = await client().chat(req(response_schema=schema))
    assert json.loads(resp.message.content) == {"n": 1}
    assert route.call_count == 2
    assert resp.usage.input_tokens == 24, "usage of both attempts is counted"
    repair_msgs = json.loads(route.calls[1].request.content)["messages"]
    assert "did not match the required JSON schema" in repair_msgs[-1]["content"]


@respx.mock
async def test_structured_output_gives_up_after_repair() -> None:
    respx.post(URL).respond(200, json=completion('{"n": "x"}'))
    with pytest.raises(StructuredOutputError):
        await client().chat(
            req(
                response_schema={
                    "type": "object",
                    "properties": {"n": {"type": "integer"}},
                    "required": ["n"],
                }
            )
        )


@respx.mock
async def test_retries_transient_errors_but_not_bad_requests() -> None:
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.ConnectError("boom"),
            httpx.Response(200, json=completion()),
        ]
    )
    assert (await client().chat(req())).message.content == "hi"
    assert route.call_count == 3
    respx.post(URL).respond(400, text="bad model")
    with pytest.raises(GatewayError, match="refused"):
        await client().chat(req())


@respx.mock
async def test_all_retries_exhausted() -> None:
    respx.post(URL).respond(502)
    with pytest.raises(GatewayError, match="after 3 attempts"):
        await client().chat(req())


@respx.mock
async def test_cost_is_converted_to_aud_and_recorded_per_run() -> None:
    respx.post(URL).respond(200, json=completion(), headers={"x-litellm-response-cost": "0.002"})
    c = client()
    meta = {"run_id": "r1", "tenant_id": "t1"}
    r1 = await c.chat(req("planner", metadata=meta))
    await c.chat(req("default", metadata=meta))
    assert r1.usage.cost_aud == pytest.approx(0.003)
    total = c.ledger.total("r1")
    assert total.input_tokens == 24
    assert total.cost_aud == pytest.approx(0.006)
    assert set(c.ledger.by_role("r1")) == {"planner", "default"}


@respx.mock
async def test_stream_assembles_text_tool_calls_and_usage() -> None:
    sse = (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"add","arguments":"{\\"a\\":"}}]}}]}\n\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"1}"}}]},"finish_reason":"tool_calls"}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":1}}\n\ndata: [DONE]\n\n'
    )
    respx.post(URL).respond(200, text=sse, headers={"content-type": "text/event-stream"})
    chunks = [c async for c in client().stream(req())]
    last = chunks[-1]
    assert last.finish_reason == "tool_calls"
    assert last.tool_calls[0].arguments == {"a": 1}
    assert last.usage is not None
    assert last.usage.input_tokens == 4
