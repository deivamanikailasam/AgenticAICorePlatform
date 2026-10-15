"""Step 8 "Done when": an external MCP client calls rag-core/search with a user token and only
sees permitted documents (the stand-in Keycloak + a test double of rag-core's search API)."""

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]
KC = os.environ.get("P0_KEYCLOAK_URL", "http://127.0.0.1:8080")
REALM = os.environ.get("P0_KEYCLOAK_REALM", "rag-platform")
ISSUER = f"{KC}/realms/{REALM}"


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait(url: str) -> None:
    for _ in range(100):
        try:
            httpx.get(url, timeout=1)
            return
        except httpx.TransportError:
            time.sleep(0.2)
    raise RuntimeError(f"{url} did not start")


def _admin_headers() -> dict[str, str]:
    tok = httpx.post(
        f"{KC}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": os.environ["KEYCLOAK_ADMIN_USER"],
            "password": os.environ["KEYCLOAK_ADMIN_PASSWORD"],
        },
    ).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _platform_secret() -> str:
    h = _admin_headers()
    cid = httpx.get(
        f"{KC}/admin/realms/{REALM}/clients", params={"clientId": "agent-platform"}, headers=h
    ).json()[0]["id"]
    return str(
        httpx.get(f"{KC}/admin/realms/{REALM}/clients/{cid}/client-secret", headers=h).json()[
            "value"
        ]
    )


def user_token(username: str) -> str:
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
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    rag_port, gw_port = _port(), _port()
    audit = tmp_path_factory.mktemp("audit") / "audit.jsonl"
    rag = subprocess.Popen(
        [sys.executable, "-m", "tests.stack.fakes.rag_core_fake", str(rag_port), ISSUER], cwd=ROOT
    )
    env = {
        **os.environ,
        "MCP_GATEWAY_PORT": str(gw_port),
        "MCP_GATEWAY_PUBLIC_URL": f"http://127.0.0.1:{gw_port}",
        "P0_RAG_CORE_URL": f"http://127.0.0.1:{rag_port}",
        "MCP_GATEWAY_AUDIT_FILE": str(audit),
        "SECRET__KEYCLOAK__AGENT_PLATFORM__CLIENT_SECRET": _platform_secret(),
    }
    gw = subprocess.Popen([sys.executable, "-m", "mcp_gateway"], env=env, cwd=ROOT)
    try:
        _wait(f"http://127.0.0.1:{gw_port}/healthz")
        yield f"http://127.0.0.1:{gw_port}/mcp", audit
    finally:
        for p in (gw, rag):
            p.terminate()
            p.wait(10)


async def _search(url: str, username: str, query: str = "refund") -> tuple[list[str], list[str]]:
    http = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {user_token(username)}"}, timeout=30
    )
    async with http, Client(streamable_http_client(url, http_client=http)) as client:
        tools = [t.name for t in (await client.list_tools()).tools]
        result = await client.call_tool("rag-core__search", {"query": query})
        assert not result.is_error, result.content
        data = result.structured_content or json.loads(result.content[0].text)  # type: ignore[union-attr]
        return tools, [r["id"] for r in data["results"]]


async def test_users_only_see_documents_they_may_read(gateway: tuple[str, Path]) -> None:
    url, _ = gateway
    tools, alice = await _search(url, "alice")
    assert "rag-core__search" in tools
    assert {"platform__calculator", "platform__clock", "platform__word_count"} <= set(tools)
    assert alice == ["fin-001", "fin-002"], "finance user sees finance documents only"
    _, bob = await _search(url, "bob")
    assert bob == ["eng-001"], "engineering user sees engineering documents only"
    _, gina = await _search(url, "gina")
    assert gina == ["glx-001"], "another tenant's user sees only her tenant's documents"


async def test_calls_without_a_token_are_rejected(gateway: tuple[str, Path]) -> None:
    url, _ = gateway
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert resp.status_code == 401


async def test_every_call_is_policy_checked_and_audited(gateway: tuple[str, Path]) -> None:
    url, audit = gateway
    await _search(url, "alice", "forecast")
    events = [json.loads(line) for line in audit.read_text().splitlines()]
    mine = [
        e
        for e in events
        if e["event"]["ctx"]["user_id"] == "alice" and e["event"]["type"] == "tool.call"
    ]
    assert mine, "tool calls are audited"
    last = mine[-1]["event"]
    assert last["payload"]["decision"] == "allow"
    assert last["payload"]["tool"] == "rag-core__search"
    assert last["ctx"]["tenant_id"] == "acme"
    assert all(e["prev_hash"] for e in events)
