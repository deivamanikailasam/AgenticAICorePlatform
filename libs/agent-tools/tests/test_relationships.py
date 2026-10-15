import json
import os
import uuid
from pathlib import Path

import httpx
import pytest
import respx
from agent_contracts.interfaces import PolicyDecisionPoint
from agent_contracts.models import (
    PolicyDecision,
    PolicyEffect,
    PolicyInput,
    RiskTier,
    RunContext,
    SideEffect,
)
from agent_tools.policy import OpaPolicyDecisionPoint
from agent_tools.relationships import (
    CompositePolicyDecisionPoint,
    OpenFgaRelationshipChecker,
    RelationshipRule,
    load_rules,
)

ROOT = Path(__file__).resolve().parents[3]
CTX = RunContext(tenant_id="acme", user_id="alice", agent_id="a1", risk_tier=RiskTier.MEDIUM)
ALICE = {"authenticated": True, "user": "alice", "groups": ["/acme/finance"], "roles": []}
RULES = {"erp__get_invoice": RelationshipRule("invoice", "invoice_id", "viewer")}
FGA = "http://fga"


class _Fixed(PolicyDecisionPoint):
    def __init__(self, effect: PolicyEffect) -> None:
        self.effect = effect

    async def decide(self, request: PolicyInput) -> PolicyDecision:
        return PolicyDecision(effect=self.effect, reasons=("primary",), policy_version="p1")


def _composite(effect: PolicyEffect = PolicyEffect.ALLOW) -> CompositePolicyDecisionPoint:
    return CompositePolicyDecisionPoint(
        _Fixed(effect), OpenFgaRelationshipChecker(FGA, "s1", api_key="k"), RULES
    )


def _req(tool: str = "erp__get_invoice", **arguments: str) -> PolicyInput:
    return PolicyInput(
        ctx=CTX, tool=tool, side_effect=SideEffect.READ, arguments=arguments, subject=ALICE
    )


def _fga(allowed: bool) -> respx.Route:
    respx.get(f"{FGA}/stores").respond(200, json={"stores": [{"id": "S1", "name": "s1"}]})
    return respx.post(f"{FGA}/stores/S1/check").respond(200, json={"allowed": allowed})


@respx.mock
async def test_relationship_present_keeps_the_primary_answer() -> None:
    route = _fga(True)
    d = await _composite().decide(_req(invoice_id="inv-1"))
    assert d.effect is PolicyEffect.ALLOW
    assert d.policy_version == "p1+openfga"
    sent = json.loads(route.calls[0].request.content)["tuple_key"]
    assert sent == {"user": "user:alice", "relation": "viewer", "object": "invoice:acme/inv-1"}
    assert route.calls[0].request.headers["authorization"] == "Bearer k"


@respx.mock
async def test_relationship_missing_denies() -> None:
    _fga(False)
    d = await _composite().decide(_req(invoice_id="inv-1"))
    assert d.effect is PolicyEffect.DENY
    assert "not viewer of invoice:acme/inv-1" in d.reasons[0]


@respx.mock
async def test_approval_stays_approval_when_related() -> None:
    _fga(True)
    d = await _composite(PolicyEffect.REQUIRE_APPROVAL).decide(_req(invoice_id="inv-1"))
    assert d.effect is PolicyEffect.REQUIRE_APPROVAL


@respx.mock
async def test_primary_deny_is_final_and_skips_openfga() -> None:
    route = _fga(True)
    d = await _composite(PolicyEffect.DENY).decide(_req(invoice_id="inv-1"))
    assert d.effect is PolicyEffect.DENY
    assert not route.called


@respx.mock
async def test_tools_without_a_rule_are_not_checked() -> None:
    route = _fga(False)
    assert (await _composite().decide(_req("calculator"))).effect is PolicyEffect.ALLOW
    assert not route.called


async def test_missing_object_argument_denies() -> None:
    d = await _composite().decide(_req())
    assert d.effect is PolicyEffect.DENY
    assert "invoice_id" in d.reasons[0]


@respx.mock
async def test_openfga_failure_denies() -> None:
    respx.get(f"{FGA}/stores").mock(side_effect=httpx.ConnectError("down"))
    d = await _composite().decide(_req(invoice_id="inv-1"))
    assert d.effect is PolicyEffect.DENY
    assert "unavailable" in d.reasons[0]


def test_repo_rules_load() -> None:
    rules = load_rules(ROOT / "policies/openfga/relations.yaml")
    assert rules["erp__post_payment"] == RelationshipRule("invoice", "invoice_id", "payer")


@pytest.mark.integration
async def test_live_opa_plus_openfga_by_user_object_and_tenant() -> None:
    """OPA decides the attributes, OpenFGA the relationship; ids never cross tenants."""
    url = os.environ.get("AGENT_OPENFGA_URL", "http://127.0.0.1:8085")
    key = os.environ["SECRET__OPENFGA__AGENT_PLATFORM__PRESHARED_KEY"]
    checker = OpenFgaRelationshipChecker(url, "agent-platform", api_key=key)
    team = f"acme-finance-{uuid.uuid4().hex[:8]}"
    inv = f"inv-{uuid.uuid4().hex[:8]}"
    await checker.write(
        [
            ("user:alice", "member", f"group:{team}"),
            (f"group:{team}", "owner_team", f"invoice:acme/{inv}"),
            ("user:alice", "payer", f"invoice:acme/{inv}"),
        ]
    )
    pdp = CompositePolicyDecisionPoint(
        OpaPolicyDecisionPoint(os.environ.get("P0_OPA_URL", "http://127.0.0.1:8181")),
        checker,
        load_rules(ROOT / "policies/openfga/relations.yaml"),
    )
    try:
        view = _req(invoice_id=inv)
        assert (await pdp.decide(view)).effect is PolicyEffect.ALLOW
        bob = view.model_copy(update={"subject": {**ALICE, "user": "bob"}})
        assert (await pdp.decide(bob)).effect is PolicyEffect.DENY
        globex = view.model_copy(update={"ctx": CTX.model_copy(update={"tenant_id": "globex"})})
        assert (await pdp.decide(globex)).effect is PolicyEffect.DENY, "same id, other tenant"
        pay = PolicyInput(
            ctx=CTX,
            tool="erp__post_payment",
            side_effect=SideEffect.IRREVERSIBLE,
            arguments={"invoice_id": inv, "amount_aud": 100},
            subject=ALICE,
        )
        d = await pdp.decide(pay)
        assert d.effect is PolicyEffect.REQUIRE_APPROVAL, "payer, and payments still need approval"
        assert (
            await pdp.decide(pay.model_copy(update={"subject": {**ALICE, "user": "bob"}}))
        ).effect is PolicyEffect.DENY
    finally:
        await checker.aclose()
