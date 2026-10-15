"""The platform MCP gateway: one MCP endpoint, one policy point, one audit point.

Exposed tools:
* ``platform__<name>``   — platform built-in tools (calculator, clock, word_count);
* ``rag-core__search``   — Project 0 retrieval, called on behalf of the user (token exchange);
* ``<server>__<tool>``   — tools of allowlisted external MCP servers, proxied (``MCP_GATEWAY_PROXY``).

Every call: verify the Keycloak token (bearer middleware) → derive tenant and subject → ask OPA →
execute through the platform tool executor → audit the decision and the outcome. Clients never
reach a tool or a downstream server directly.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import mcp_types as types
from agent_contracts.interfaces import AuditSink, PolicyDecisionPoint, Tool, ToolRegistry
from agent_contracts.models import AuditEvent, PolicyEffect, PolicyInput, SideEffect, ToolCall
from agent_spec.platform import PlatformConfig
from agent_tools.auth import KeycloakTokenVerifier
from agent_tools.builtin import BUILTIN_TOOLS
from agent_tools.executor import PlatformToolExecutor
from agent_tools.identity import TokenExchanger, subject_token
from agent_tools.mcp import McpClientPool
from agent_tools.ragcore import RagCoreSearchTool
from agent_tools.registry import InMemoryToolRegistry
from agent_tools.secrets import EnvSecretResolver, SecretResolver
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_gateway.identity import context_for
from mcp_gateway.settings import Settings

IDEMPOTENCY_META = "io.agent-platform/idempotency-key"
RUN_META = "io.agent-platform/run-id"


class _Renamed(Tool):
    """Expose a tool under the gateway's namespaced name, keeping its spec otherwise."""

    def __init__(self, inner: Tool, name: str) -> None:
        self.inner = inner
        self._spec = inner.spec.model_copy(update={"name": name})

    @property
    def spec(self) -> Any:
        return self._spec

    async def invoke(self, arguments: Any, ctx: Any) -> Any:
        return await self.inner.invoke(arguments, ctx)


class Gateway:
    def __init__(
        self,
        settings: Settings,
        platform: PlatformConfig,
        pdp: PolicyDecisionPoint,
        audit: AuditSink,
        secrets: SecretResolver | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.platform = platform
        self.pdp = pdp
        self.audit = audit
        self.secrets = secrets or EnvSecretResolver()
        self.registry = registry or InMemoryToolRegistry()
        self.executor = PlatformToolExecutor(self.registry)
        self.pool = McpClientPool(platform, audit=audit, secrets=self.secrets)
        self.exchanger = TokenExchanger(
            settings.token_url, settings.client_id, settings.client_secret, self.secrets
        )

    async def load_tools(self) -> list[str]:
        for cls in BUILTIN_TOOLS:
            tool = cls()
            await self.registry.register(_Renamed(tool, f"platform__{tool.spec.name}"))
        await self.registry.register(
            _Renamed(
                RagCoreSearchTool(self.settings.rag_core_url, self.exchanger), "rag-core__search"
            )
        )
        for server in self.settings.proxy_servers:
            await self.pool.register_all(self.registry, server)
        return [s.name for s in await self.registry.list()]

    async def list_tools(self, _ctx: Any, _params: Any) -> types.ListToolsResult:
        tools = []
        for spec in await self.registry.list():
            ann = types.ToolAnnotations(
                read_only_hint=spec.side_effect is SideEffect.READ,
                destructive_hint=spec.side_effect is SideEffect.IRREVERSIBLE,
                idempotent_hint=spec.idempotent,
            )
            tools.append(
                types.Tool(
                    name=spec.name,
                    description=spec.description,
                    input_schema=spec.input_schema,
                    annotations=ann,
                )
            )
        return types.ListToolsResult(tools=tools)

    async def call_tool(
        self, _ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        token = get_access_token()
        if token is None:  # the bearer middleware already rejects these; defence in depth
            return _error("authentication required")
        raw_meta: Any = params.meta or {}
        meta = dict(raw_meta) if isinstance(raw_meta, dict) else raw_meta.model_dump(by_alias=True)
        try:
            ctx, subject = context_for(token, run_id=meta.get(RUN_META))
        except PermissionError as exc:
            return _error(str(exc))
        name, arguments = params.name, dict(params.arguments or {})
        started = time.perf_counter()
        try:
            spec = (await self.registry.get(name)).spec
        except KeyError:
            return _error(f"unknown tool {name}")
        decision = await self.pdp.decide(
            PolicyInput(
                ctx=ctx,
                tool=name,
                side_effect=spec.side_effect,
                data_classification=spec.data_classification,
                arguments=arguments,
                subject=subject,
            )
        )
        payload: dict[str, Any] = {
            "via": "mcp-gateway",
            "tool": name,
            "ref": spec.ref,
            "decision": decision.effect.value,
            "reasons": list(decision.reasons),
            "client_id": token.client_id,
        }
        if decision.effect is not PolicyEffect.ALLOW:
            await self.audit.append(AuditEvent(type="policy.decision", ctx=ctx, payload=payload))
            msg = (
                "approval required"
                if decision.effect is PolicyEffect.REQUIRE_APPROVAL
                else "denied"
            )
            return _error(f"{msg}: {'; '.join(decision.reasons)}")
        reset = subject_token.set(token.token)
        try:
            result = await self.executor.execute(
                ToolCall(
                    name=name, arguments=arguments, idempotency_key=meta.get(IDEMPOTENCY_META)
                ),
                ctx,
            )
        finally:
            subject_token.reset(reset)
        payload |= {
            "ok": result.ok,
            "error_code": result.error_code.value if result.error_code else None,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        await self.audit.append(AuditEvent(type="tool.call", ctx=ctx, payload=payload))
        if not result.ok:
            return _error(
                f"{result.error_code.value if result.error_code else 'failed'}: {result.error}"
            )
        structured = result.output if isinstance(result.output, dict) else {"result": result.output}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(structured, default=str))],
            structured_content=structured,
            is_error=False,
        )

    def server(self) -> Server[Any]:
        return Server(
            "agent-platform-mcp-gateway",
            version="0.1.0",
            on_list_tools=self.list_tools,
            on_call_tool=self.call_tool,
        )

    def app(self) -> Starlette:
        s = self.settings
        inner = self.server().streamable_http_app(
            host=s.host,
            auth=AuthSettings(issuer_url=s.issuer, resource_server_url=f"{s.public_url}/mcp"),  # type: ignore[arg-type]
            token_verifier=KeycloakTokenVerifier(s.issuer, audience=s.audience),
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=list(s.allowed_hosts),
                allowed_origins=[],
            ),
            custom_starlette_routes=[Route("/healthz", _health)],
        )
        original = inner.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app: Starlette) -> AsyncIterator[None]:
            await self.load_tools()
            async with original(app):
                yield
            await self.pool.aclose()

        inner.router.lifespan_context = lifespan
        return inner


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], is_error=True
    )


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})
