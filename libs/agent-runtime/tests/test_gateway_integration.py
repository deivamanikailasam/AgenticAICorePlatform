"""Against the gateway: the stand-in (mock models) in CI, or Project 0's LiteLLM on the Mac."""

import json
import os
from datetime import date

import pytest
from agent_contracts.models import ChatRequest, Message, ModelCapabilities, Role
from agent_runtime.models import GatewayModelClient
from agent_spec.platform import Currency, Gateway, Models, PlatformConfig, load_platform

pytestmark = pytest.mark.integration
CAPS = ModelCapabilities(tool_calling=True, structured_output=True, streaming=True)
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "confidence": {"type": "number"}},
    "required": ["answer", "confidence"],
}


def _client() -> GatewayModelClient:
    base = load_platform()
    roles = dict(base.models.roles)
    caps = {**base.models.capabilities, "local-qwen-json": CAPS}
    cfg = PlatformConfig(
        gateway=Gateway(url=os.environ.get("P0_LITELLM_URL", "http://127.0.0.1:4000")),
        models=Models(roles=roles, capabilities=caps),
        currency=Currency(
            usd_to_aud=base.currency.usd_to_aud,
            source=base.currency.source,
            as_of=date.fromisoformat(str(base.currency.as_of)),
        ),
    )
    key = os.environ["LITELLM_MASTER_KEY"]
    return GatewayModelClient(cfg, keys={"default": key})


def _req(model: str, **kw: object) -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=(Message(role=Role.USER, content="Say hello."),),
        metadata={"run_id": "it-1"},
        **kw,
    )  # type: ignore[arg-type]


async def test_every_role_resolves_and_answers() -> None:
    c = _client()
    for role in ("default", "planner", "executor", "router", "judge", "summariser"):
        resp = await c.chat(_req(role))
        assert resp.message.content, role
        assert resp.usage.input_tokens > 0
    assert c.ledger.calls("it-1") == 6
    assert c.ledger.total("it-1").total_tokens > 0


async def test_stream_through_gateway() -> None:
    chunks = [ch async for ch in _client().stream(_req("default"))]
    assert "".join(ch.delta for ch in chunks).strip()
    assert chunks[-1].finish_reason == "stop"


async def test_structured_output_through_gateway() -> None:
    resp = await _client().chat(_req("local-qwen-json", response_schema=SCHEMA))
    assert json.loads(resp.message.content)["answer"] == "stand-in"
