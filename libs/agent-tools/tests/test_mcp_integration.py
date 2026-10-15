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


@pytest.fixture(scope="module")
def remote_url() -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_servers.remote_http", str(port), ISSUER],
        cwd=Path(__file__).parent,
    )
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
    assert {"execute_tool read_text_file", "execute_tool get_customer"} <= names
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
