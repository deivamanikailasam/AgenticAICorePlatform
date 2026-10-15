"""Step 16 against the stand-in stack: the agentic RAG agent searches as each user, with tokens
exchanged by Keycloak for the rag-core audience, and only ever sees and cites documents that
user may read (rag-core double from tests/stack/fakes; scripted model)."""

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.models import Message, NodeKind, PlanNode, Role, RunContext
from agent_runtime.agentic_rag import AgenticRagStrategy
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.executor import PlatformToolExecutor
from agent_tools.identity import TokenExchanger, subject_token
from agent_tools.ragcore import RagCoreSearchTool
from agent_tools.registry import InMemoryToolRegistry
from agent_tools.secrets import EnvSecretResolver

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]
KC = os.environ.get("P0_KEYCLOAK_URL", "http://127.0.0.1:8080")
REALM = os.environ.get("P0_KEYCLOAK_REALM", "rag-platform")
ISSUER = f"{KC}/realms/{REALM}"
REF = "${secret:keycloak/agent-platform#client_secret}"


def _admin() -> dict[str, str]:
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


def _client_secret() -> str:
    h = _admin()
    cid = httpx.get(
        f"{KC}/admin/realms/{REALM}/clients", params={"clientId": "agent-platform"}, headers=h
    ).json()[0]["id"]
    return str(
        httpx.get(f"{KC}/admin/realms/{REALM}/clients/{cid}/client-secret", headers=h).json()[
            "value"
        ]
    )


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
def rag_core() -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.stack.fakes.rag_core_fake", str(port), ISSUER], cwd=ROOT
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.post(f"{url}/v1/search", timeout=1)
            break
        except httpx.TransportError:
            time.sleep(0.2)
    yield url
    proc.terminate()
    proc.wait(10)


def say(obj: Any) -> Message:
    return Message(role=Role.ASSISTANT, content=json.dumps(obj))


async def _ask(url: str, user: str, exchanger: TokenExchanger, cite: list[str]) -> dict[str, Any]:
    reg = InMemoryToolRegistry()
    await reg.register(RagCoreSearchTool(url, exchanger))
    tools = AuthorisingToolExecutor(
        PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), MemoryAuditSink()
    )
    model = ScriptedModelClient(
        [
            say(
                {
                    "retrieve": True,
                    "steps": [{"id": "Q1", "query": "refund", "architecture": "naive"}],
                }
            ),
            say({"answer": "Here is what the documents say.", "citations": cite}),
            say({"grounded": True}),
        ]
    )
    node = PlanNode(
        id="kb.loop",
        kind=NodeKind.AGENT,
        config={"loop": "agentic_rag", "tools": ["search"], "max_iterations": 6, "config": {}},
    )
    ctx = RunContext(tenant_id="acme", user_id=user, agent_id="knowledge-assistant")
    reset = subject_token.set(_user_token(user))
    try:
        out: dict[str, Any] = await AgenticRagStrategy(model, tools, reg)(
            node, {"input": {"question": "What is the refund policy?"}}, ctx
        )
    finally:
        subject_token.reset(reset)
    return out


async def test_each_user_retrieves_and_cites_only_what_they_may_read(
    rag_core: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SECRET__KEYCLOAK__AGENT_PLATFORM__CLIENT_SECRET", _client_secret())
    exchanger = TokenExchanger(
        f"{ISSUER}/protocol/openid-connect/token", "agent-platform", REF, EnvSecretResolver()
    )
    try:
        # the model tries to cite everything, including documents the user cannot see
        everything = ["fin-001", "fin-002", "eng-001", "glx-001"]
        alice = await _ask(rag_core, "alice", exchanger, everything)
        bob = await _ask(rag_core, "bob", exchanger, everything)
    finally:
        await exchanger.aclose()
    assert alice["hops"][0]["results"] == ["fin-001", "fin-002"]
    assert [c["id"] for c in alice["citations"]] == ["fin-001", "fin-002"]
    assert bob["hops"][0]["results"] == ["eng-001"]
    assert [c["id"] for c in bob["citations"]] == ["eng-001"]
