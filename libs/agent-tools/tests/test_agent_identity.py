"""Step 29 (ASI03): per-agent service identities, two-hop delegated exchange, and where secrets
come from. Unit tests with mock transports; the live checks are in
``services/agent-api/tests/test_agent_identity_live.py``."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from agent_tools.identity import (
    AgentIdentities,
    DelegatingExchanger,
    TokenExchanger,
    acting_agent,
)
from agent_tools.secrets import (
    ChainedSecretResolver,
    EnvSecretResolver,
    SecretResolver,
    default_resolver,
)


class Fixed(SecretResolver):
    async def resolve(self, reference: str) -> str:
        return f"secret-for-{reference}"


def keycloak(seen: list[dict[str, Any]]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        seen.append({"client": req.headers["authorization"], **form})
        if form["grant_type"] == "client_credentials":
            return httpx.Response(200, json={"access_token": "agent-sa", "expires_in": 300})
        token = f"{form['subject_token']}>{form['audience']}"
        return httpx.Response(200, json={"access_token": token, "expires_in": 300})

    return httpx.MockTransport(handler)


def _client_of(entry: dict[str, Any]) -> str:
    import base64

    return base64.b64decode(entry["client"].split()[1]).decode().split(":")[0]


async def test_delegation_goes_user_to_platform_to_agent_to_service() -> None:
    seen: list[dict[str, Any]] = []
    transport = keycloak(seen)
    platform = TokenExchanger(
        "http://kc/token", "agent-platform", "s", Fixed(), transport=transport
    )
    identities = AgentIdentities("http://kc/token", Fixed(), transport=transport)
    ex = DelegatingExchanger(platform, identities)

    reset = acting_agent.set("invoice-triage")
    try:
        token = await ex.exchange("alice", "rag-core")
    finally:
        acting_agent.reset(reset)
    assert token.access_token == "alice>agent-invoice-triage>rag-core"
    first, second = seen
    assert (_client_of(first), first["audience"], first["scope"]) == (
        "agent-platform",
        "agent-invoice-triage",
        "agent-platform:agent-invoice-triage",
    )
    assert (_client_of(second), second["audience"], second["scope"]) == (
        "agent-invoice-triage",
        "rag-core",
        "agent-platform:rag-core",
    )
    assert second["subject_token"] == "alice>agent-invoice-triage"  # never the user's own token

    seen.clear()
    assert (
        await ex.exchange("bob", "rag-core")
    ).access_token == "bob>rag-core"  # no agent: one hop
    assert [_client_of(s) for s in seen] == ["agent-platform"]
    await ex.aclose()


async def test_agent_service_token_uses_its_own_client_and_secret() -> None:
    seen: list[dict[str, Any]] = []
    identities = AgentIdentities("http://kc/token", Fixed(), transport=keycloak(seen))
    assert await identities.service_token("triage") == "agent-sa"
    assert await identities.service_token("triage") == "agent-sa"  # cached
    assert len(seen) == 1
    assert _client_of(seen[0]) == "agent-triage"
    assert identities.secret_ref("triage") == "${secret:keycloak/agent-triage#client_secret}"
    auth = identities.bao_auth("triage")
    assert auth.role == "agent-triage"
    assert await auth.jwt() == "agent-sa"
    await identities.aclose()


async def test_the_authorising_executor_names_the_acting_agent_per_call() -> None:
    from agent_contracts.dummies.observability import MemoryAuditSink
    from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
    from agent_contracts.interfaces import Tool
    from agent_contracts.models import RunContext, ToolCall, ToolSpec
    from agent_tools.authorising import AuthorisingToolExecutor, ToolGrant
    from agent_tools.executor import PlatformToolExecutor
    from agent_tools.registry import InMemoryToolRegistry

    class WhoAmI(Tool):
        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(name="whoami")

        async def invoke(self, arguments: Any, ctx: RunContext) -> Any:
            return {"agent": acting_agent.get()}

    reg = InMemoryToolRegistry()
    await reg.register(WhoAmI())

    def executor(grants: Any) -> AuthorisingToolExecutor:
        return AuthorisingToolExecutor(
            PlatformToolExecutor(reg),
            reg,
            RulesPolicyDecisionPoint(),
            MemoryAuditSink(),
            grants=grants,
        )

    ctx = RunContext(tenant_id="acme", user_id="alice", agent_id="triage")
    as_agent = await executor({"whoami": ToolGrant()}).execute(ToolCall(name="whoami"), ctx)
    assert as_agent.output == {"agent": "triage"}
    assert acting_agent.get() is None
    as_person = await executor(None).execute(ToolCall(name="whoami"), ctx)  # the MCP gateway
    assert as_person.output == {"agent": None}


def test_default_resolver_uses_openbao_and_refuses_env_outside_dev() -> None:
    from agent_tools.openbao import OpenBaoSecretResolver

    bao = {"P0_OPENBAO_URL": "http://bao", "AGENT_BAO_ROLE_ID": "r", "AGENT_BAO_SECRET_ID": "s"}
    assert isinstance(default_resolver({"AGENT_ENV": "dev"}), EnvSecretResolver)
    assert isinstance(default_resolver({"AGENT_ENV": "dev", **bao}), ChainedSecretResolver)
    assert isinstance(default_resolver({"AGENT_ENV": "prod", **bao}), OpenBaoSecretResolver)
    for stage in ("staging", "prod"):
        with pytest.raises(RuntimeError, match="secrets come only from OpenBao"):
            default_resolver({"AGENT_ENV": stage})


async def test_chained_resolver_falls_through_on_missing_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Empty(SecretResolver):
        async def resolve(self, reference: str) -> str:
            raise KeyError("not in openbao")

    monkeypatch.setenv("SECRET__A__B", "from-env")
    chain = ChainedSecretResolver(Empty(), EnvSecretResolver())
    assert await chain.resolve("${secret:a#b}") == "from-env"
    with pytest.raises(KeyError, match="not in openbao"):
        await chain.resolve("${secret:x#y}")
