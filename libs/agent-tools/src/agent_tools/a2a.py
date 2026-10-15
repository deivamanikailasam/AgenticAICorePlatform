"""A2A (Agent2Agent v1.x) for the platform (Step 24): signing our Agent Cards, and calling
allowlisted remote agents as tools.

A remote agent is a tool ``a2a://<name>`` only when ``platform.yaml`` lists it under
``a2a.agents`` with pinned card keys. Before every first use (and again when the cached card is
older than ``CARD_TTL``) its Agent Card is fetched and its signature checked against those keys;
an unsigned card, a card signed by another key, or an agent that is not listed is refused.

Calls go through the platform tool executor like any tool (OPA per call, the agent's grants,
audit, rate limits); the remote agent's reply comes back as untrusted data. Outbound
credentials follow the agent's ``auth``: the user's token exchanged for the remote audience
(``on_behalf_of``), client credentials, a static bearer token from the secret store, or none.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from agent_contracts.interfaces import Tool
from agent_contracts.models import DataClass, RunContext, SideEffect, ToolSpec
from agent_spec.platform import A2aAgent

from agent_tools.identity import TokenExchanger
from agent_tools.secrets import SecretResolver, maybe_secret

CARD_TTL = 300.0  # seconds a verified card is reused before it is fetched and checked again


class A2aRefusedError(PermissionError):
    """A remote agent was refused: not listed, unsigned, or signed by an unknown key."""


def card_signer(private_key_pem: str | bytes, key_id: str) -> Callable[[Any], Any]:
    """Signs an AgentCard (ES256, JWS over the canonical card) with the platform's key."""
    from a2a.utils.signing import create_agent_card_signer

    return create_agent_card_signer(
        private_key_pem, {"kid": key_id, "alg": "ES256", "jku": None, "typ": "JOSE"}
    )


def card_verifier(agent: A2aAgent) -> Callable[[Any], None]:
    """Accepts a card only with a valid signature from one of ``agent``'s pinned keys."""
    from a2a.utils.signing import create_signature_verifier

    def key_for(kid: str | None, jku: str | None) -> str:
        if kid is None or kid not in agent.card_keys:
            raise A2aRefusedError(f"A2A agent {agent.name}: card signed with unknown key {kid!r}")
        return agent.card_keys[kid]

    return create_signature_verifier(key_for, list(agent.algorithms))


async def client_credentials_token(
    agent: A2aAgent, secrets: SecretResolver, http: httpx.AsyncClient
) -> str:
    auth = agent.auth
    secret = await maybe_secret(auth.client_secret, secrets)
    if not (auth.issuer and auth.client_id and secret):
        raise A2aRefusedError(
            f"A2A agent {agent.name}: client credentials need issuer, client_id, secret"
        )
    resp = await http.post(
        f"{auth.issuer.rstrip('/')}/protocol/openid-connect/token",
        data={"grant_type": "client_credentials", **({"scope": auth.scope} if auth.scope else {})},
        auth=(auth.client_id, secret),
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


class A2aRemoteAgentTool(Tool):
    """One allowlisted remote A2A agent as a platform tool."""

    def __init__(
        self,
        agent: A2aAgent,
        secrets: SecretResolver,
        exchanger: TokenExchanger | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        grpc_channel_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.agent = agent
        self.secrets = secrets
        self.exchanger = exchanger
        self.transport = transport
        self.grpc_channel_factory = grpc_channel_factory
        self._card: Any = None
        self._card_at = 0.0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.agent.name,
            ref=f"a2a://{self.agent.name}",
            version="1.0.0",
            description=self.agent.description or f"Ask the remote agent {self.agent.name}.",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string", "minLength": 1, "maxLength": 20000}},
                "required": ["message"],
                "additionalProperties": False,
            },
            side_effect=SideEffect(self.agent.side_effect),
            idempotent=False,
            timeout_seconds=self.agent.timeout_seconds,
            data_classification=DataClass.CONFIDENTIAL,
            owner="agent-platform",
            tags=("external", "a2a"),
        )

    async def _headers(self, http: httpx.AsyncClient) -> dict[str, str]:
        auth = self.agent.auth
        if auth.kind == "none":
            return {}
        if auth.kind == "bearer":
            token = await maybe_secret(auth.token, self.secrets)
            if not token:
                raise A2aRefusedError(f"A2A agent {self.agent.name}: bearer auth needs a token")
            return {"Authorization": f"Bearer {token}"}
        if auth.kind == "on_behalf_of":
            from agent_tools.identity import subject_token

            user_token = subject_token.get()
            if user_token is None or self.exchanger is None or not auth.audience:
                raise A2aRefusedError(
                    f"A2A agent {self.agent.name} acts on behalf of the user and there is no "
                    "user token or exchanger; refusing to call it with a service identity"
                )
            exchanged = await self.exchanger.exchange(user_token, auth.audience)
            return {"Authorization": f"Bearer {exchanged.access_token}"}
        return {
            "Authorization": f"Bearer {await client_credentials_token(self.agent, self.secrets, http)}"
        }

    async def card(self, http: httpx.AsyncClient) -> Any:
        """The remote agent's card, fetched and signature-checked (cached for ``CARD_TTL``)."""
        from a2a.client.card_resolver import A2ACardResolver
        from a2a.utils.signing import SignatureVerificationError

        if self._card is not None and time.monotonic() - self._card_at < CARD_TTL:
            return self._card
        resolver = A2ACardResolver(http, self.agent.url)
        try:
            card = await resolver.get_agent_card(signature_verifier=card_verifier(self.agent))
        except SignatureVerificationError as exc:
            raise A2aRefusedError(
                f"A2A agent {self.agent.name}: Agent Card refused ({exc})"
            ) from exc
        self._card, self._card_at = card, time.monotonic()
        return card

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        from a2a.client import ClientCallContext, ClientConfig, create_client
        from a2a.types import Message, Part, Role, SendMessageRequest

        async with httpx.AsyncClient(
            timeout=self.agent.timeout_seconds, transport=self.transport, base_url=self.agent.url
        ) as http:
            card = await self.card(http)
            # sent as HTTP headers or gRPC metadata, whichever binding is used
            call = ClientCallContext(service_parameters=await self._headers(http))
            client = await create_client(
                card,
                client_config=ClientConfig(
                    httpx_client=http,
                    supported_protocol_bindings=[self.agent.binding],
                    grpc_channel_factory=self.grpc_channel_factory or grpc_channel,
                    streaming=False,
                ),
            )
            request = SendMessageRequest(
                message=Message(
                    role=Role.ROLE_USER,
                    message_id=uuid.uuid4().hex,
                    context_id=ctx.thread_id,
                    parts=[Part(text=str(arguments["message"]))],
                )
            )
            responses = [r async for r in client.send_message(request, context=call)]
            return reply_of(responses, self.agent.name)


def grpc_channel(target: str) -> Any:
    """Plain-text gRPC only to loopback (local development); TLS everywhere else."""
    import grpc

    host = target.removeprefix("grpc://").rsplit(":", 1)[0].strip("[]")
    target = target.removeprefix("grpc://")
    if host in ("127.0.0.1", "localhost", "::1"):
        return grpc.aio.insecure_channel(target)
    return grpc.aio.secure_channel(target, grpc.ssl_channel_credentials())


def reply_of(responses: list[Any], agent: str) -> dict[str, Any]:
    """The remote agent's answer from A2A stream responses: final task state and its text."""
    from a2a.types import TaskState

    state, task_id, texts = "unknown", None, []
    for r in responses:
        kind = r.WhichOneof("payload")
        if kind == "task":
            task_id = r.task.id
            state = TaskState.Name(r.task.status.state)
            texts += [p.text for a in r.task.artifacts for p in a.parts if p.text]
        elif kind == "message":
            texts += [p.text for p in r.message.parts if p.text]
        elif kind == "status_update":
            task_id = r.status_update.task_id
            state = TaskState.Name(r.status_update.status.state)
            msg = r.status_update.status.message
            texts += [p.text for p in msg.parts if p.text]
        elif kind == "artifact_update":
            texts += [p.text for p in r.artifact_update.artifact.parts if p.text]
    return {"agent": agent, "task_id": task_id, "state": state, "text": "\n".join(texts)}
