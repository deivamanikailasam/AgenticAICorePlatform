"""Remote A2A agents as tools (Step 24): the Agent Card must be signed by a pinned key before
any message is sent; credentials follow the agent's ``auth`` and never fall back to a service
identity; the reply comes back as data."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part
from agent_contracts.models import RunContext, SideEffect
from agent_spec.platform import A2aAgent
from agent_tools.a2a import A2aRefusedError, A2aRemoteAgentTool, card_signer
from agent_tools.secrets import EnvSecretResolver
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.applications import Starlette


def keypair() -> tuple[bytes, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public.decode()


KEY, PUB = keypair()
ROGUE, _ = keypair()


class Echo(AgentExecutor):
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []  # (authorization, text)

    async def execute(self, context: RequestContext, event_queue: Any) -> None:
        assert context.message is not None
        headers = context.call_context.state.get("headers", {}) if context.call_context else {}
        self.seen.append((str(headers.get("authorization", "")), context.get_user_input()))
        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.add_artifact([Part(text=f"echo: {context.get_user_input()}")])
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: Any) -> None:
        return None


def remote(sign_with: bytes | None, kid: str = "k1") -> tuple[httpx.ASGITransport, Echo]:
    echo = Echo()
    card = AgentCard(
        name="echo",
        description="echoes",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url="http://echo.test/a2a")
        ],
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[AgentSkill(id="echo", name="echo", description="echoes", tags=["test"])],
    )
    if sign_with is not None:
        card = card_signer(sign_with, kid)(card)
    handler = DefaultRequestHandlerV2(echo, InMemoryTaskStore(), card)
    app = Starlette(
        routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/a2a")]
    )
    return httpx.ASGITransport(app=app), echo


def entry(**overrides: Any) -> A2aAgent:
    return A2aAgent.model_validate(
        {"name": "echo", "url": "http://echo.test", "card_keys": {"k1": PUB}, **overrides}
    )


def ctx() -> RunContext:
    return RunContext(tenant_id="acme", user_id="alice", agent_id="caller")


async def test_a_signed_remote_agent_answers_with_the_configured_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET__PARTNER__ECHO__TOKEN", "partner-token")
    transport, echo = remote(KEY)
    tool = A2aRemoteAgentTool(
        entry(auth={"kind": "bearer", "token": "${secret:partner/echo#token}"}),
        EnvSecretResolver(),
        transport=transport,
    )
    assert tool.spec.ref == "a2a://echo"
    assert tool.spec.side_effect is SideEffect.WRITE  # remote agents act: never "read" by default
    assert "external" in tool.spec.tags
    reply = await tool.invoke({"message": "hello"}, ctx())
    assert reply["state"] == "TASK_STATE_COMPLETED"
    assert reply["text"] == "echo: hello"
    assert echo.seen == [("Bearer partner-token", "hello")]
    uuid.UUID(reply["task_id"])


async def test_an_unsigned_card_is_refused_before_any_message() -> None:
    transport, echo = remote(None)
    tool = A2aRemoteAgentTool(entry(), EnvSecretResolver(), transport=transport)
    with pytest.raises(A2aRefusedError, match="Agent Card refused"):
        await tool.invoke({"message": "hi"}, ctx())
    assert echo.seen == []


@pytest.mark.parametrize(("key", "kid"), [(ROGUE, "k1"), (KEY, "k-unpinned")])
async def test_a_card_signed_by_an_unpinned_key_is_refused(key: bytes, kid: str) -> None:
    transport, echo = remote(key, kid)
    tool = A2aRemoteAgentTool(entry(), EnvSecretResolver(), transport=transport)
    with pytest.raises(A2aRefusedError):
        await tool.invoke({"message": "hi"}, ctx())
    assert echo.seen == []


async def test_on_behalf_of_never_falls_back_to_a_service_identity() -> None:
    transport, echo = remote(KEY)
    tool = A2aRemoteAgentTool(
        entry(auth={"kind": "on_behalf_of", "audience": "partner"}),
        EnvSecretResolver(),
        transport=transport,
    )
    with pytest.raises(A2aRefusedError, match="no user token"):
        await tool.invoke({"message": "hi"}, ctx())
    assert echo.seen == []


def test_a_listed_agent_needs_at_least_one_pinned_key() -> None:
    with pytest.raises(ValueError, match="card_keys"):
        A2aAgent.model_validate({"name": "echo", "url": "http://echo.test", "card_keys": {}})
