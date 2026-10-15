"""Step 7 "Done when": two external MCP servers — one local (stdio), one remote (HTTP + OAuth) —
are discovered, called, traced and audited; an unlisted server is refused.

* local: the official filesystem server, @modelcontextprotocol/server-filesystem@1.0.0 via npx
  (a handshake-era server: tests negotiation down to 2025-11-25);
* remote: an SDK server on Streamable HTTP that requires a Keycloak client-credentials token
  (tests OAuth discovery and the 2026-07-28 revision).
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.models import RunContext, SideEffect, ToolCall
from agent_spec.platform import Currency, Mcp, McpAuth, McpServer, Models, PlatformConfig
from agent_tools.executor import PlatformToolExecutor
from agent_tools.mcp import McpClientPool, McpServerRefusedError
from agent_tools.registry import InMemoryToolRegistry
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

pytestmark = pytest.mark.integration
KC = os.environ.get("P0_KEYCLOAK_URL", "http://127.0.0.1:8080")
REALM = os.environ.get("P0_KEYCLOAK_REALM", "rag-platform")
ISSUER = f"{KC}/realms/{REALM}"
CTX = RunContext(tenant_id="t-int", user_id="u1", agent_id="a1")


def _client_secret() -> str:
    tok = httpx.post(
        f"{KC}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": os.environ["KEYCLOAK_ADMIN_USER"],
            "password": os.environ["KEYCLOAK_ADMIN_PASSWORD"],
        },
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    cid = httpx.get(
        f"{KC}/admin/realms/{REALM}/clients", params={"clientId": "agent-platform"}, headers=h
    ).json()[0]["id"]
    return str(
        httpx.get(f"{KC}/admin/realms/{REALM}/clients/{cid}/client-secret", headers=h).json()[
            "value"
        ]
    )


def _start_remote(*extra: str) -> tuple[subprocess.Popen[bytes], int]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_servers.remote_http", str(port), ISSUER, *extra],
        cwd=Path(__file__).parent,
    )
    return proc, port


@pytest.fixture(scope="module")
def remote_url() -> Iterator[str]:
    proc, port = _start_remote()
    url = f"http://127.0.0.1:{port}/mcp"
    for _ in range(50):
        try:
            httpx.get(
                f"http://127.0.0.1:{port}/.well-known/oauth-protected-resource/mcp", timeout=1
            )
            break
        except httpx.TransportError:
            time.sleep(0.2)
    yield url
    proc.terminate()
    proc.wait(10)


@pytest.fixture
async def pool(
    remote_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[McpClientPool]:
    (tmp_path / "notes.txt").write_text("quarterly numbers are in the finance folder")
    monkeypatch.setenv("SECRET__KEYCLOAK__AGENT_PLATFORM__CLIENT_SECRET", _client_secret())
    servers = [
        McpServer(
            name="filesystem",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem@1.0.0", str(tmp_path)],
            side_effects={"read_text_file": "read", "list_directory": "read"},
            allowed_tools=["read_text_file", "list_directory"],
            timeout_seconds=60,
        ),
        McpServer(
            name="crm",
            transport="http",
            url=remote_url,
            trusted=True,
            auth=McpAuth(
                kind="oauth_client_credentials",
                issuer=ISSUER,
                client_id="agent-platform",
                client_secret="${secret:keycloak/agent-platform#client_secret}",
            ),
        ),
    ]
    roles = {r: "m" for r in ("default", "planner", "executor", "router", "judge", "summariser")}
    cfg = PlatformConfig(
        models=Models(roles=roles),
        currency=Currency(usd_to_aud=1.5, source="t", as_of=date(2026, 10, 1)),
        mcp=Mcp(servers=servers),
    )
    p = McpClientPool(cfg, audit=MemoryAuditSink())
    p.tmp = tmp_path  # type: ignore[attr-defined]
    yield p
    await p.aclose()


async def test_two_external_servers_discovered_called_traced_audited(
    pool: McpClientPool, span_exporter: InMemorySpanExporter
) -> None:
    exporter = span_exporter

    reg = InMemoryToolRegistry()
    fs = await pool.register_all(reg, "filesystem")
    crm = await pool.register_all(reg, "crm")
    assert {s.name for s in fs} == {"filesystem__read_text_file", "filesystem__list_directory"}
    by_name = {s.name: s for s in crm}
    assert by_name["crm__get_customer"].side_effect is SideEffect.READ
    assert by_name["crm__email_customer"].side_effect is SideEffect.IRREVERSIBLE, (
        "destructive hint from a trusted server"
    )

    assert await pool.protocol_version("filesystem") == "2025-11-25"
    assert await pool.protocol_version("crm") == "2026-07-28"

    ex = PlatformToolExecutor(reg)
    path = str(pool.tmp / "notes.txt")  # type: ignore[attr-defined]
    read = await ex.execute(
        ToolCall(name="filesystem__read_text_file", arguments={"path": path}), CTX
    )
    assert read.ok, read.error
    assert "quarterly numbers" in str(read.output)
    cust = await ex.execute(
        ToolCall(name="crm__get_customer", arguments={"customer_id": "c-42"}), CTX
    )
    assert cust.ok, cust.error
    assert "Ada Lovelace" in str(cust.output)

    names = {s.name for s in exporter.get_finished_spans()}
    assert {"tools/call read_text_file", "tools/call get_customer"} <= names
    assert pool.audit is not None
    audited = [
        (r.event.payload["server"], r.event.payload["ok"])
        for r in await pool.audit.read(CTX.run_id)
    ]
    assert audited == [("filesystem", True), ("crm", True)]
    assert await pool.audit.verify()


async def test_unlisted_server_refused(pool: McpClientPool) -> None:
    with pytest.raises(McpServerRefusedError):
        await pool.discover("random-internet-server")


async def test_remote_server_rejects_calls_without_a_token(remote_url: str) -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            remote_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert resp.status_code == 401


def _user_token(username: str) -> str:
    resp = httpx.post(
        f"{ISSUER}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "mcp-inspector",
            "username": username,
            "password": "standin-only",
        },
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


@pytest.fixture(scope="module")
def obo_port() -> Iterator[int]:
    """A resource server that accepts only tokens issued for the `rag-core` audience."""
    proc, port = _start_remote("rag-core")
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/mcp", timeout=1)
            break
        except httpx.TransportError:
            time.sleep(0.2)
    yield port
    proc.terminate()
    proc.wait(10)


async def test_on_behalf_of_server_sees_the_user_and_never_the_platform(
    obo_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 10: an MCP server that accepts only `aud=rag-core` tokens gets the user's own rights."""
    from agent_tools.identity import TokenExchanger, subject_token
    from agent_tools.secrets import EnvSecretResolver

    port = obo_port
    monkeypatch.setenv("SECRET__KEYCLOAK__AGENT_PLATFORM__CLIENT_SECRET", _client_secret())
    ref = "${secret:keycloak/agent-platform#client_secret}"
    server = McpServer(
        name="obo",
        transport="http",
        url=f"http://127.0.0.1:{port}/mcp",
        trusted=True,
        auth=McpAuth(
            kind="on_behalf_of",
            audience="rag-core",
            issuer=ISSUER,
            client_id="agent-platform",
            client_secret=ref,
            scope="agent-platform:rag-core",
        ),
    )
    roles = {r: "m" for r in ("default", "planner", "executor", "router", "judge", "summariser")}
    cfg = PlatformConfig(
        models=Models(roles=roles),
        currency=Currency(usd_to_aud=1.5, source="t", as_of=date(2026, 10, 1)),
        mcp=Mcp(servers=[server]),
    )
    secrets = EnvSecretResolver()
    exchanger = TokenExchanger(
        f"{ISSUER}/protocol/openid-connect/token", "agent-platform", ref, secrets
    )
    audit = MemoryAuditSink()
    pool = McpClientPool(cfg, audit=audit, secrets=secrets, exchanger=exchanger)
    reg = InMemoryToolRegistry()
    try:
        await pool.register_all(reg, "obo")
        ex = PlatformToolExecutor(reg)
        anonymous = await ex.execute(ToolCall(name="obo__whoami"), CTX)
        assert not anonymous.ok
        assert "service identity" in (anonymous.error or "")

        reset = subject_token.set(_user_token("alice"))
        try:
            as_alice = await ex.execute(ToolCall(name="obo__whoami"), CTX)
        finally:
            subject_token.reset(reset)
        assert as_alice.ok, as_alice.error
        assert as_alice.output["user"] == "alice"
        assert as_alice.output["client_id"] == "agent-platform"
        events = [r.event.payload for r in await audit.read(CTX.run_id)]
        assert [e["ok"] for e in events] == [False, True]
        assert all(e["on_behalf_of"] for e in events)
    finally:
        await pool.aclose()
        await exchanger.aclose()
