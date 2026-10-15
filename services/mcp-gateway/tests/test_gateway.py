"""Gateway policy and audit paths without a network (fake policy, in-memory audit)."""

from datetime import date
from typing import Any

import mcp_types as types
import pytest
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.interfaces import PolicyDecisionPoint
from agent_contracts.models import PolicyDecision, PolicyEffect, PolicyInput
from agent_spec.platform import Currency, Models, PlatformConfig
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp_gateway.app import Gateway
from mcp_gateway.identity import tenant_of
from mcp_gateway.settings import Settings


class FixedPolicy(PolicyDecisionPoint):
    def __init__(self, effect: PolicyEffect) -> None:
        self.effect = effect
        self.seen: list[PolicyInput] = []

    async def decide(self, request: PolicyInput) -> PolicyDecision:
        self.seen.append(request)
        return PolicyDecision(effect=self.effect, reasons=("test rule",))


def platform() -> PlatformConfig:
    roles = {r: "m" for r in ("default", "planner", "executor", "router", "judge", "summariser")}
    return PlatformConfig(
        models=Models(roles=roles),
        currency=Currency(usd_to_aud=1.5, source="t", as_of=date(2026, 10, 1)),
    )


async def gateway(effect: PolicyEffect) -> tuple[Gateway, FixedPolicy, MemoryAuditSink]:
    pdp, audit = FixedPolicy(effect), MemoryAuditSink()
    gw = Gateway(Settings(), platform(), pdp, audit)
    await gw.load_tools()
    return gw, pdp, audit


def as_user(groups: list[str]) -> Any:
    token = AccessToken(
        token="t",
        client_id="mcp-inspector",
        scopes=[],
        subject="u-1",
        claims={"preferred_username": "alice", "groups": groups},
    )
    return auth_context_var.set(AuthenticatedUser(token))


async def call(gw: Gateway, name: str, args: dict[str, Any]) -> types.CallToolResult:
    return await gw.call_tool(None, types.CallToolRequestParams(name=name, arguments=args))


def test_tenant_comes_from_the_group_path() -> None:
    assert tenant_of(["/acme/finance", "/acme/engineering"]) == "acme"
    assert tenant_of([]) == "default"
    with pytest.raises(PermissionError, match="several tenants"):
        tenant_of(["/acme/finance", "/globex/finance"])


async def test_allowed_call_runs_and_is_audited() -> None:
    gw, pdp, audit = await gateway(PolicyEffect.ALLOW)
    reset = as_user(["/acme/finance"])
    try:
        result = await call(gw, "platform__calculator", {"expression": "6*7"})
    finally:
        auth_context_var.reset(reset)
    assert not result.is_error
    assert result.structured_content == {"result": 42.0}
    seen = pdp.seen[0]
    assert seen.ctx.tenant_id == "acme"
    assert seen.subject["authenticated"] is True
    assert seen.subject["groups"] == ["/acme/finance"]
    [record] = await audit.read()
    assert record.event.type == "tool.call"
    assert record.event.payload["decision"] == "allow"


@pytest.mark.parametrize(
    ("effect", "message"),
    [(PolicyEffect.DENY, "denied"), (PolicyEffect.REQUIRE_APPROVAL, "approval required")],
)
async def test_refused_calls_never_reach_the_tool(effect: PolicyEffect, message: str) -> None:
    gw, _, audit = await gateway(effect)
    reset = as_user(["/acme/finance"])
    try:
        result = await call(gw, "platform__calculator", {"expression": "1+1"})
    finally:
        auth_context_var.reset(reset)
    assert result.is_error
    assert message in result.content[0].text  # type: ignore[union-attr]
    [record] = await audit.read()
    assert record.event.type == "policy.decision"


async def test_rag_core_refuses_without_a_user_token_exchange() -> None:
    gw, _, _ = await gateway(PolicyEffect.ALLOW)
    gw.exchanger = None  # type: ignore[assignment]
    reset = as_user(["/acme/finance"])
    try:
        result = await call(gw, "rag-core__search", {"query": "refund"})
    finally:
        auth_context_var.reset(reset)
    assert result.is_error, "never falls back to a service identity"


async def test_tools_are_listed_with_side_effect_annotations() -> None:
    gw, _, _ = await gateway(PolicyEffect.ALLOW)
    listed = {t.name: t for t in (await gw.list_tools(None, None)).tools}
    assert listed["rag-core__search"].annotations is not None
    assert listed["rag-core__search"].annotations.read_only_hint is True


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("opa", "OpaPolicyDecisionPoint"),
        ("cerbos", "CerbosPolicyDecisionPoint"),
        ("opa+openfga", "CompositePolicyDecisionPoint"),
        ("cerbos+openfga", "CompositePolicyDecisionPoint"),
    ],
)
def test_policy_engine_is_chosen_by_configuration(
    name: str, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    from mcp_gateway.__main__ import build_pdp

    monkeypatch.setenv("MCP_GATEWAY_PDP", name)
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("AGENT_OPENFGA_RULES", str(root / "policies/openfga/relations.yaml"))
    assert type(build_pdp(Settings())).__name__ == kind


@pytest.mark.parametrize("name", ["openfga", "opa+cerbos", "none"])
def test_unknown_policy_engine_refuses_to_start(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_gateway.__main__ import build_pdp

    monkeypatch.setenv("MCP_GATEWAY_PDP", name)
    with pytest.raises(SystemExit):
        build_pdp(Settings())
