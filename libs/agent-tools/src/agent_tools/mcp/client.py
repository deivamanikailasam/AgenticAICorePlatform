"""``McpClientPool``: the only way the platform talks to MCP servers.

* **Allowlist** — servers come from ``platform.yaml`` ``mcp.servers``; any other is refused.
* **Negotiation** — ``protocol: auto`` tries the stateless 2026-07-28 revision first and falls
  back to the 2025-11-25 initialize handshake for older servers (including servers that drop the
  connection when probed); ``legacy`` and ``2026-07-28`` pin a mode.
* **Discovery and namespacing** — tools become ``mcp://<server>/<tool>`` with model-safe names
  ``<server>__<tool>``; ``allowed_tools`` narrows what is exposed.
* **Trust** — annotations (read-only, destructive, idempotent) are believed only for trusted
  servers; otherwise side effects come from ``side_effects`` or default to ``write``. Tool
  descriptions from untrusted servers are clipped and treated as untrusted text.
* **On behalf of the user** — for ``auth.kind: on_behalf_of`` servers each call exchanges the
  caller's token (``subject_token``) for one valid only for ``auth.audience`` and uses it on a
  connection of its own; with no user token the call is refused, never sent as the platform.
* **Every call** is traced (MCP conventions: ``tools/call {tool}``, a CLIENT span, under the
  platform's ``execute_tool`` span when an agent calls it) and audited (arguments hashed, never
  stored).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any
from urllib.parse import urlparse

from agent_contracts.interfaces import AuditSink, ToolRegistry
from agent_contracts.models import AuditEvent, RunContext, SideEffect, ToolSpec, arguments_digest
from agent_spec.platform import McpServer, PlatformConfig
from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from opentelemetry import trace

from agent_tools.identity import Exchanger, subject_token
from agent_tools.mcp.auth import http_client_for
from agent_tools.secrets import SecretResolver, default_resolver

MAX_UNTRUSTED_DESCRIPTION = 1024
_tracer = trace.get_tracer("agent_platform.tools.mcp")


class McpServerRefusedError(PermissionError):
    """The server is not on the platform's MCP allowlist."""


class McpToolError(RuntimeError):
    """The MCP server reported an error for a tool call (``isError: true``)."""


def model_name(server: str, tool: str) -> str:
    raw = f"{server}__{tool}"
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)
    if len(safe) > 64:
        safe = safe[:55] + "_" + hashlib.sha256(raw.encode()).hexdigest()[:8]
    return safe


def stdio_environment(server: McpServer) -> dict[str, str]:
    """The SDK's minimal environment, plus allowlisted platform variables, plus the server's own."""
    env = get_default_environment()
    env.update({k: os.environ[k] for k in server.env_passthrough if k in os.environ})
    env.update(server.env)
    return env


class _Connection:
    """One live connection, owned by its own background task.

    The MCP SDK uses anyio task groups, which must be entered and exited by the same task; a
    dedicated owner task lets any request task use the client and any task close it.
    """

    def __init__(
        self, server: McpServer, secrets: SecretResolver, bearer_override: str | None = None
    ) -> None:
        self.server = server
        self.secrets = secrets
        self.bearer_override = bearer_override
        self.client: Client | None = None
        self.lock = asyncio.Lock()
        self._stop: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    async def _enter(self, stack: AsyncExitStack, mode: str) -> Client:
        if self.server.transport == "stdio":
            if not self.server.command:
                raise ValueError(f"MCP server {self.server.name}: stdio needs a command")
            target: Any = StdioServerParameters(
                command=self.server.command,
                args=self.server.args,
                env=stdio_environment(self.server),
            )
        else:
            if not self.server.url:
                raise ValueError(f"MCP server {self.server.name}: http needs a url")
            http = await stack.enter_async_context(
                await http_client_for(self.server, self.secrets, self.bearer_override)
            )
            target = streamable_http_client(self.server.url, http_client=http)
        client = Client(
            target, mode=mode, read_timeout_seconds=self.server.timeout_seconds, cache=None
        )
        await stack.enter_async_context(client)
        return client

    async def _owner(self, mode: str, ready: asyncio.Future[Client], stop: asyncio.Event) -> None:
        try:
            async with AsyncExitStack() as stack:
                ready.set_result(await self._enter(stack, mode))
                await stop.wait()
        except BaseException as exc:  # surface start-up failures to the caller waiting on `ready`
            if not ready.done():
                ready.set_exception(exc)
            elif not isinstance(exc, asyncio.CancelledError | GeneratorExit):
                raise

    async def _open(self, mode: str) -> Client:
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[Client] = loop.create_future()
        stop = asyncio.Event()
        task = asyncio.create_task(self._owner(mode, ready, stop), name=f"mcp:{self.server.name}")
        try:
            client = await asyncio.wait_for(asyncio.shield(ready), self.server.timeout_seconds)
        except BaseException:
            stop.set()
            task.cancel()
            raise
        self._stop, self._task = stop, task
        return client

    async def get(self) -> Client:
        async with self.lock:
            if self.client is not None:
                return self.client
            if self.server.protocol == "auto":
                try:
                    self.client = await self._open("auto")
                except (MCPError, OSError, TimeoutError, ExceptionGroup):
                    # older servers can drop the connection when probed: retry with the handshake
                    self.client = await self._open("legacy")
            else:
                self.client = await self._open(self.server.protocol)
            return self.client

    async def close(self) -> None:
        if self._stop is not None and self._task is not None:
            self._stop.set()
            try:
                await asyncio.wait_for(self._task, 10)
            except (TimeoutError, asyncio.CancelledError, Exception):
                self._task.cancel()
        self.client, self._stop, self._task = None, None, None


class McpClientPool:
    def __init__(
        self,
        platform: PlatformConfig,
        audit: AuditSink | None = None,
        secrets: SecretResolver | None = None,
        exchanger: Exchanger | None = None,
    ) -> None:
        self.platform = platform
        self.audit = audit
        self.secrets = secrets or default_resolver()
        self.exchanger = exchanger
        self._conns: dict[str, _Connection] = {}

    def server(self, name: str) -> McpServer:
        cfg = self.platform.mcp.server(name)
        if cfg is None:
            raise McpServerRefusedError(
                f"MCP server '{name}' is not on the allowlist (platform.yaml mcp.servers)"
            )
        return cfg

    async def client(self, name: str) -> Client:
        cfg = self.server(name)
        conn = self._conns.setdefault(name, _Connection(cfg, self.secrets))
        return await conn.get()

    async def protocol_version(self, name: str) -> str:
        return str((await self.client(name)).protocol_version)

    # ------------------------------------------------------------ discovery

    def _side_effect(self, cfg: McpServer, tool: Any) -> tuple[SideEffect, bool]:
        if tool.name in cfg.side_effects:
            se = SideEffect(cfg.side_effects[tool.name])
            return se, se is SideEffect.READ
        ann = tool.annotations if cfg.trusted else None
        if ann is not None and ann.read_only_hint:
            return SideEffect.READ, True
        if ann is not None and ann.destructive_hint:
            return SideEffect.IRREVERSIBLE, False
        return SideEffect.WRITE, bool(ann is not None and ann.idempotent_hint)

    async def discover(self, name: str) -> list[ToolSpec]:
        cfg = self.server(name)
        client = await self.client(name)
        listed = []
        cursor: str | None = None
        for _ in range(100):  # page cap: a misbehaving server cannot loop us forever
            page = await client.list_tools(cursor=cursor) if cursor else await client.list_tools()
            listed += page.tools
            cursor = getattr(page, "next_cursor", None)
            if not cursor:
                break
        specs = []
        for tool in listed:
            if cfg.allowed_tools is not None and tool.name not in cfg.allowed_tools:
                continue
            side_effect, idempotent = self._side_effect(cfg, tool)
            description = tool.description or ""
            if not cfg.trusted:
                description = description[:MAX_UNTRUSTED_DESCRIPTION]
            specs.append(
                ToolSpec(
                    name=model_name(name, tool.name),
                    ref=f"mcp://{name}/{tool.name}",
                    version="1.0.0",
                    description=description,
                    input_schema=dict(tool.input_schema or {"type": "object"}),
                    output_schema=dict(tool.output_schema)
                    if tool.output_schema is not None
                    else None,
                    side_effect=side_effect,
                    idempotent=idempotent,
                    timeout_seconds=cfg.timeout_seconds,
                    owner=f"mcp:{name}",
                )
            )
        return specs

    async def register_all(
        self,
        registry: ToolRegistry,
        name: str,
        screen: Callable[[ToolSpec], Awaitable[str | None]] | None = None,
    ) -> list[ToolSpec]:
        """Register the server's tools. With ``screen`` (Step 27), a tool whose description or
        parameter descriptions carry an injection is not registered, so no model ever sees it;
        the refusal is audited."""
        from agent_tools.mcp.tool import McpTool

        specs = await self.discover(name)
        kept = []
        for spec in specs:
            reason = await screen(spec) if screen is not None else None
            if reason:
                if self.audit is not None:
                    await self.audit.append(
                        AuditEvent(
                            type="mcp.tool_refused",
                            ctx=RunContext(
                                tenant_id="platform", user_id="system", agent_id="mcp-pool"
                            ),
                            payload={"server": name, "tool": spec.name, "reason": reason},
                        )
                    )
                continue
            await registry.register(McpTool(spec, self))
            kept.append(spec)
        return kept

    # ------------------------------------------------------------ calls

    async def _on_behalf_of(self, cfg: McpServer) -> _Connection:
        user_token = subject_token.get()
        if user_token is None:
            raise PermissionError(
                f"MCP server {cfg.name} acts on behalf of the user and there is no user token; "
                "refusing to call it with a service identity"
            )
        if self.exchanger is None or not cfg.auth.audience:
            raise PermissionError(
                f"MCP server {cfg.name}: on_behalf_of needs a token exchanger and auth.audience"
            )
        exchanged = await self.exchanger.exchange(user_token, cfg.auth.audience)
        return _Connection(cfg, self.secrets, bearer_override=exchanged.access_token)

    async def call(
        self, ref: str, arguments: dict[str, Any], ctx: RunContext, call_id: str | None = None
    ) -> Any:
        server_name, _, tool = ref.removeprefix("mcp://").partition("/")
        cfg = self.server(server_name)
        started = time.perf_counter()
        args_hash = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, default=str).encode()
        ).hexdigest()
        attrs: dict[str, str | int] = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool,
            "mcp.method.name": "tools/call",
            "network.transport": "pipe" if cfg.transport == "stdio" else "tcp",
            "agent_platform.tool.ref": ref,
            "agent_platform.tenant.id": ctx.tenant_id,
        }
        if call_id:
            attrs["gen_ai.tool.call.id"] = call_id
        if cfg.url:
            parsed = urlparse(cfg.url)
            attrs["server.address"] = parsed.hostname or ""
            if parsed.port:
                attrs["server.port"] = parsed.port
        ok, error, version = False, None, None
        with _tracer.start_as_current_span(
            f"tools/call {tool}", kind=trace.SpanKind.CLIENT, attributes=attrs
        ) as span:
            own: _Connection | None = None
            try:
                # defence in depth: only allowlisted tools are registered, and a direct call for
                # any other tool is refused here too, before the server is contacted (Step 33)
                if cfg.allowed_tools and tool not in cfg.allowed_tools:
                    raise McpServerRefusedError(
                        f"MCP tool '{tool}' is not on server '{server_name}''s allowlist"
                    )
                if cfg.auth.kind == "on_behalf_of":
                    own = await self._on_behalf_of(cfg)
                    client = await own.get()
                else:
                    client = await self.client(server_name)
                version = str(client.protocol_version)
                span.set_attribute("mcp.protocol.version", version)
                result = await client.call_tool(tool, arguments)
                if result.is_error:
                    text = " ".join(getattr(c, "text", "") for c in result.content)[:500]
                    raise McpToolError(text or "MCP tool reported an error")
                ok = True
                if getattr(result, "structured_content", None) is not None:
                    return result.structured_content
                return {
                    "content": [
                        c.model_dump(mode="json", exclude_none=True) for c in result.content
                    ]
                }
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:500]
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(trace.Status(trace.StatusCode.ERROR, error))
                raise
            finally:
                if own is not None:
                    await own.close()
                if self.audit is not None:
                    await self.audit.append(
                        AuditEvent(
                            # the transport record; the platform's tool.call is written by the
                            # authorising executor (Step 31)
                            type="mcp.call",
                            ctx=ctx,
                            payload={
                                "transport": "mcp",
                                "server": server_name,
                                "tool": tool,
                                "ref": ref,
                                "protocol_version": version,
                                "arguments_sha256": args_hash,
                                "arguments_digest": arguments_digest(dict(arguments)),
                                "ok": ok,
                                "error": error,
                                "call_id": call_id,
                                "on_behalf_of": cfg.auth.kind == "on_behalf_of",
                                "duration_ms": int((time.perf_counter() - started) * 1000),
                            },
                        )
                    )

    async def aclose(self) -> None:
        for conn in self._conns.values():
            await conn.close()
        self._conns.clear()
