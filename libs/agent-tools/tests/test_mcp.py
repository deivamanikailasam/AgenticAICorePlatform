"""MCP client against local stdio servers (no network): negotiation, trust, allowlist, trace, audit."""

import sys
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.models import RunContext, SideEffect, ToolCall, ToolErrorCode
from agent_spec.platform import Currency, Mcp, McpServer, Models, PlatformConfig
from agent_tools.executor import PlatformToolExecutor
from agent_tools.mcp import McpClientPool, McpServerRefusedError
from agent_tools.mcp.client import model_name
from agent_tools.registry import InMemoryToolRegistry
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

SERVERS = Path(__file__).parent / "mcp_servers"
CTX = RunContext(tenant_id="t1", user_id="u1", agent_id="a1")
EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(_provider)


def platform(*servers: McpServer) -> PlatformConfig:
    roles = {r: "m" for r in ("default", "planner", "executor", "router", "judge", "summariser")}
    return PlatformConfig(
        models=Models(roles=roles),
        currency=Currency(usd_to_aud=1.5, source="t", as_of=date(2026, 10, 1)),
        mcp=Mcp(servers=list(servers)),
    )


LEGACY = McpServer(
    name="legacy",
    transport="stdio",
    command=sys.executable,
    args=[str(SERVERS / "legacy_stdio.py")],
)
MODERN = McpServer(
    name="math",
    transport="stdio",
    command=sys.executable,
    args=[str(SERVERS / "modern_stdio.py")],
    trusted=True,
)


@pytest.fixture
async def pool() -> AsyncIterator[McpClientPool]:
    p = McpClientPool(platform(LEGACY, MODERN), audit=MemoryAuditSink())
    yield p
    await p.aclose()


async def test_negotiates_modern_and_falls_back_to_handshake(pool: McpClientPool) -> None:
    assert await pool.protocol_version("math") == "2026-07-28"
    assert await pool.protocol_version("legacy") == "2025-11-25"


async def test_unlisted_server_is_refused(pool: McpClientPool) -> None:
    with pytest.raises(McpServerRefusedError, match="not on the allowlist"):
        await pool.discover("evil")
    with pytest.raises(McpServerRefusedError):
        await pool.call("mcp://evil/steal", {}, CTX)


async def test_discovery_namespaces_and_trusts_only_trusted_annotations(
    pool: McpClientPool,
) -> None:
    math = {s.name: s for s in await pool.discover("math")}
    assert math["math__add"].ref == "mcp://math/add"
    assert math["math__add"].side_effect is SideEffect.READ, "trusted server: annotation believed"
    assert math["math__add"].idempotent
    legacy = {s.name: s for s in await pool.discover("legacy")}
    assert legacy["legacy__echo"].side_effect is SideEffect.WRITE, "untrusted: readOnlyHint ignored"
    assert legacy["legacy__wipe_disk"].side_effect is SideEffect.WRITE
    assert len(legacy["legacy__wipe_disk"].description) <= 1024, (
        "untrusted descriptions are clipped"
    )


async def test_allowed_tools_and_side_effect_overrides() -> None:
    cfg = LEGACY.model_copy(update={"allowed_tools": ["echo"], "side_effects": {"echo": "read"}})
    p = McpClientPool(platform(cfg))
    try:
        specs = await p.discover("legacy")
        assert [s.name for s in specs] == ["legacy__echo"]
        assert specs[0].side_effect is SideEffect.READ
    finally:
        await p.aclose()


async def test_calls_through_the_executor_are_traced_and_audited(pool: McpClientPool) -> None:
    assert pool.audit is not None
    reg = InMemoryToolRegistry()
    await pool.register_all(reg, "math")
    await pool.register_all(reg, "legacy")
    ex = PlatformToolExecutor(reg)
    EXPORTER.clear()
    added = await ex.execute(ToolCall(name="math__add", arguments={"a": 2, "b": 3}), CTX)
    assert added.ok
    assert added.output == {"result": 5.0}
    echoed = await ex.execute(
        ToolCall(name="legacy__echo", arguments={"text": "hi"}, idempotency_key="e1"), CTX
    )
    assert echoed.output == {"content": [{"type": "text", "text": "hi"}]}
    failed = await ex.execute(ToolCall(name="math__broken", idempotency_key="b1"), CTX)
    assert failed.error_code is ToolErrorCode.FAILED, "an MCP isError result is a failed tool call"

    spans = {s.name: s for s in EXPORTER.get_finished_spans()}
    add_span = spans["execute_tool add"]
    assert add_span.attributes is not None
    assert add_span.attributes["mcp.protocol.version"] == "2026-07-28"
    assert spans["execute_tool echo"].attributes["mcp.protocol.version"] == "2025-11-25"  # type: ignore[index]

    records = await pool.audit.read(CTX.run_id)
    assert [(r.event.payload["tool"], r.event.payload["ok"]) for r in records] == [
        ("add", True),
        ("echo", True),
        ("broken", False),
    ]
    assert "a" not in str(records[0].event.payload.get("arguments", "")), (
        "arguments are hashed, never stored"
    )
    assert len(records[0].event.payload["arguments_sha256"]) == 64
    assert await pool.audit.verify()


def test_model_names_are_safe_and_bounded() -> None:
    assert model_name("rag-core", "search") == "rag-core__search"
    long = model_name("s" * 40, "t" * 40)
    assert len(long) <= 64
    assert long != model_name("s" * 40, "t" * 39 + "u")


def test_stdio_servers_get_only_allowlisted_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_tools.mcp.client import stdio_environment

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-should-never-leak")
    env = stdio_environment(LEGACY.model_copy(update={"env": {"FS_ROOT": "/data"}}))
    assert env["HTTPS_PROXY"] == "http://proxy:3128"
    assert env["FS_ROOT"] == "/data"
    assert "LITELLM_MASTER_KEY" not in env
