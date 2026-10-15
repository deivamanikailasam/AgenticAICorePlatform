"""The A2A gateway: publishes the agents whose spec sets ``interop.a2a_publish`` as A2A v1.x
agents.

For each published agent ``<name>``:

* ``GET  /agents/<name>/.well-known/agent-card.json`` — its Agent Card, signed (ES256) with the
  platform's key: skill, endpoints per binding, the Keycloak security scheme. Public.
* ``POST /agents/<name>/jsonrpc`` — the JSON-RPC binding;
* ``/agents/<name>/rest/...`` — the HTTP+JSON binding;
* gRPC on its own port for one chosen agent (``grpc_server``).

Every call (not the card) passes ``Inbound``: Keycloak token (401), then OPA ``a2a.inbound``
(403). The task runs through the run API as the caller (``RunApiExecutor``). Push notifications
go only to hosts in ``a2a.publish.push_allowed_hosts``, over HTTPS (plain HTTP only to loopback).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import grpc
import httpx
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import (
    DefaultRequestHandlerV2,
    GrpcHandler,
    GrpcServerCallContextBuilder,
)
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes, create_rest_routes
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)
from a2a.types import AgentCard
from agent_spec import AgentSpec
from agent_spec.platform import A2aPublish
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from a2a_gateway.cards import agent_card, signed
from a2a_gateway.executor import RunApiExecutor
from a2a_gateway.inbound import Inbound

AGENT_PATH = re.compile(r"^/agents/([^/]+)(/.*)?$")
CARD = "/.well-known/agent-card.json"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def push_url_validator(allowed_hosts: list[str]) -> Callable[[str], Awaitable[bool]]:
    """Push notifications are outbound requests the caller chooses: only to allowlisted hosts
    (no SSRF into the platform's network), HTTPS except to loopback."""

    async def valid(url: str) -> bool:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if host not in allowed_hosts:
            return False
        return parts.scheme == "https" or (parts.scheme == "http" and host in LOOPBACK)

    return valid


class _Guard:
    """ASGI middleware: ``Inbound`` before any A2A call; cards, health and index are public."""

    def __init__(self, app: ASGIApp, inbound: Inbound) -> None:
        self.app = app
        self.inbound = inbound

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        match = AGENT_PATH.match(scope.get("path", "")) if scope["type"] == "http" else None
        if match is None or (match.group(2) == CARD and match.group(1) in self.inbound.published):
            await self.app(scope, receive, send)
            return
        admission = await self.inbound.admit(
            Headers(scope=scope).get("authorization"), match.group(1)
        )
        if not admission.ok:
            headers = (
                {"WWW-Authenticate": 'Bearer realm="agent-platform"'}
                if admission.status == 401
                else {}
            )
            await JSONResponse(
                {"error": admission.reason}, status_code=admission.status, headers=headers
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


class A2aGateway:
    def __init__(
        self,
        specs: Mapping[str, AgentSpec],
        publish: A2aPublish,
        inbound: Inbound,
        signer: Callable[[Any], Any],
        api: httpx.AsyncClient,
        issuer: str,
        grpc_agent: str = "",
        grpc_public: str = "",
        push_http: httpx.AsyncClient | None = None,
    ) -> None:
        self.specs = {n: s for n, s in specs.items() if s.interop.a2a_publish}
        self.publish = publish
        self.inbound = inbound
        self.api = api
        self.push_http = push_http or httpx.AsyncClient(timeout=10)
        self.validator = push_url_validator(publish.push_allowed_hosts)
        self.cards: dict[str, AgentCard] = {
            name: signed(
                agent_card(
                    spec,
                    publish.base_url,
                    issuer,
                    grpc_url=grpc_public if name == grpc_agent and grpc_public else None,
                ),
                signer,
            )
            for name, spec in self.specs.items()
        }
        self.handlers = {name: self._handler(name) for name in self.specs}

    @property
    def published(self) -> list[str]:
        return sorted(self.specs)

    def _handler(self, name: str) -> DefaultRequestHandlerV2:
        store = InMemoryPushNotificationConfigStore()
        return DefaultRequestHandlerV2(
            RunApiExecutor(name, self.api),
            InMemoryTaskStore(),
            self.cards[name],
            push_config_store=store,
            push_sender=BasePushNotificationSender(
                self.push_http, store, push_url_validator=self.validator
            ),
            push_url_validator=self.validator,
        )

    def routes(self) -> list[BaseRoute]:
        routes: list[BaseRoute] = [Route("/healthz", _health), Route("/", self._index)]
        for name, handler in self.handlers.items():
            # each agent in its own mount, so no route of one agent can match another's path;
            # the SDK's path-tenant mount (``/{tenant}``) is dropped: the tenant comes from the
            # verified token, never from the URL
            rest = [
                r
                for r in create_rest_routes(handler, path_prefix="/rest")
                if getattr(r, "path", "") != "/{tenant}"
            ]
            agent_routes = [
                *create_agent_card_routes(self.cards[name], card_url=CARD),
                *create_jsonrpc_routes(handler, "/jsonrpc"),
                *rest,
            ]
            routes.append(Mount(f"/agents/{name}", routes=agent_routes))
        return routes

    def app(self) -> ASGIApp:
        return _Guard(Starlette(routes=self.routes()), self.inbound)

    async def _index(self, _request: Request) -> JSONResponse:
        base = self.publish.base_url.rstrip("/")
        return JSONResponse(
            {"agents": [{"name": n, "card": f"{base}/agents/{n}{CARD}"} for n in self.published]}
        )

    def grpc_server(self, agent: str, port: int, host: str = "127.0.0.1") -> Any:
        """gRPC binding for one published agent, with the same gate as HTTP."""
        from a2a.types import a2a_pb2_grpc

        if agent not in self.specs:
            raise ValueError(f"{agent!r} is not a published agent")
        server = grpc.aio.server(interceptors=[_GrpcGuard(self.inbound, agent)])
        a2a_pb2_grpc.add_A2AServiceServicer_to_server(  # type: ignore[no-untyped-call]
            GrpcHandler(self.handlers[agent], context_builder=_GrpcContext()), server
        )
        server.add_insecure_port(f"{host}:{port}")
        return server


class _GrpcContext(GrpcServerCallContextBuilder):
    """Carries the (already admitted) caller's authorization metadata to the executor."""

    def build(self, context: Any) -> ServerCallContext:
        md = {k: v for k, v in (context.invocation_metadata() or ())}
        return ServerCallContext(state={"headers": {"authorization": md.get("authorization", "")}})


class _GrpcGuard(grpc.aio.ServerInterceptor):  # type: ignore[misc]  # grpcio is untyped
    """grpc.aio server interceptor applying ``Inbound``: UNAUTHENTICATED / PERMISSION_DENIED."""

    def __init__(self, inbound: Inbound, agent: str) -> None:
        self.inbound = inbound
        self.agent = agent

    async def intercept_service(self, continuation: Any, details: Any) -> Any:
        md = {k: v for k, v in (details.invocation_metadata or ())}
        admission = await self.inbound.admit(md.get("authorization"), self.agent)
        if admission.ok:
            return await continuation(details)
        code = (
            grpc.StatusCode.UNAUTHENTICATED
            if admission.status == 401
            else grpc.StatusCode.PERMISSION_DENIED
        )
        handler = await continuation(details)

        async def refuse(_request: Any, context: Any) -> Any:
            await context.abort(code, admission.reason)

        async def refuse_stream(_request: Any, context: Any) -> Any:
            await context.abort(code, admission.reason)
            yield  # pragma: no cover

        if handler is None:
            return None
        if handler.response_streaming:
            return grpc.unary_stream_rpc_method_handler(
                refuse_stream,
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )
        return grpc.unary_unary_rpc_method_handler(
            refuse,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})
