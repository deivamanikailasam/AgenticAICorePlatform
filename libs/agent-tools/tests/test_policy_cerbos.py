import os
from typing import Any

import httpx
import pytest
import respx
from agent_contracts.models import (
    DataClass,
    PolicyEffect,
    PolicyInput,
    RiskTier,
    RunContext,
    SideEffect,
)
from agent_contracts.testing import PolicyDecisionPointContract
from agent_tools.policy import OpaPolicyDecisionPoint
from agent_tools.policy_cerbos import CerbosPolicyDecisionPoint, cerbos_request

CERBOS = os.environ.get("AGENT_CERBOS_URL", "http://127.0.0.1:3592")
CTX = RunContext(tenant_id="acme", user_id="alice", agent_id="a1", risk_tier=RiskTier.MEDIUM)
ALICE = {"authenticated": True, "user": "alice", "groups": ["/acme/finance"], "roles": []}


def _answer(call: str, no_approval: str, *outputs: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "actions": {"call": call, "call_without_approval": no_approval},
                    "outputs": [{"src": "r", "val": o} for o in outputs],
                    "meta": {"actions": {"call": {"matchedPolicy": "resource.tool.vdefault"}}},
                }
            ]
        },
    )


@respx.mock
@pytest.mark.parametrize(
    ("answer", "effect", "approvers"),
    [
        (_answer("EFFECT_ALLOW", "EFFECT_ALLOW", {"reason": "read tool allowed"}), "allow", None),
        (
            _answer(
                "EFFECT_ALLOW",
                "EFFECT_DENY",
                {"approval": "irreversible action", "approvers": "group:two-person-approvers"},
            ),
            "require_approval",
            "group:two-person-approvers",
        ),
        (_answer("EFFECT_DENY", "EFFECT_DENY", {"deny": "cross-tenant access"}), "deny", None),
    ],
)
async def test_two_actions_map_to_three_effects(
    answer: httpx.Response, effect: str, approvers: str | None
) -> None:
    route = respx.post("http://cerbos/api/check/resources").mock(return_value=answer)
    d = await CerbosPolicyDecisionPoint("http://cerbos").decide(
        PolicyInput(ctx=CTX, tool="t", side_effect=SideEffect.READ, subject=ALICE)
    )
    assert d.effect.value == effect
    assert d.approvers == approvers
    assert d.policy_version == "cerbos:resource.tool.vdefault"
    assert route.called


def test_every_attribute_is_sent_even_when_absent() -> None:
    body = cerbos_request(PolicyInput(ctx=CTX, tool="t"))
    attr = body["resources"][0]["resource"]["attr"]
    assert {"resource_tenant_id", "allowed_tools", "side_effect", "arguments"} <= set(attr)
    assert attr["approval"]["approved"] is False  # no evidence: the approval rules still apply
    assert attr["side_effect"] == ""  # unknown: the policy denies it
    assert body["principal"]["attr"]["authenticated"] is False


@respx.mock
@pytest.mark.parametrize(
    "response",
    [httpx.Response(500), httpx.Response(200, json={}), httpx.Response(200, json={"results": []})],
)
async def test_fails_closed(response: httpx.Response) -> None:
    respx.post("http://cerbos/api/check/resources").mock(return_value=response)
    d = await CerbosPolicyDecisionPoint("http://cerbos").decide(
        PolicyInput(ctx=CTX, tool="t", side_effect=SideEffect.READ, subject=ALICE)
    )
    assert d.effect is PolicyEffect.DENY


@respx.mock
async def test_unreachable_cerbos_denies() -> None:
    respx.post("http://cerbos/api/check/resources").mock(side_effect=httpx.ConnectError("down"))
    d = await CerbosPolicyDecisionPoint("http://cerbos").decide(PolicyInput(ctx=CTX, tool="t"))
    assert d.effect is PolicyEffect.DENY
    assert "unavailable" in d.reasons[0]


@pytest.mark.integration
class TestCerbosContract(PolicyDecisionPointContract):
    """Against Cerbos with policies/cerbos loaded (`just up-alternatives`)."""

    def make(self) -> CerbosPolicyDecisionPoint:
        return CerbosPolicyDecisionPoint(CERBOS)


def _case(**kw: Any) -> PolicyInput:
    base: dict[str, Any] = {
        "ctx": CTX,
        "tool": "rag-core__search",
        "side_effect": SideEffect.READ,
        "data_classification": DataClass.INTERNAL,
        "subject": ALICE,
    }
    ctx_update = kw.pop("ctx", {})
    base.update(kw)
    base["ctx"] = CTX.model_copy(update=ctx_update)
    return PolicyInput(**base)


APPROVED = {
    "approval_id": "run-1:pay.approval",
    "approved": True,
    "approver": "j.smith",
    "approver_groups": ["/acme/approvers"],
    "bound": True,
}

PARITY = {
    "authenticated read": _case(),
    "anonymous": _case(subject={}),
    "cross-tenant": _case(resource_tenant_id="globex"),
    "same tenant explicit": _case(resource_tenant_id="acme"),
    "not granted": _case(allowed_tools=("calculator",)),
    "granted": _case(allowed_tools=("rag-core__search",)),
    "restricted without clearance": _case(data_classification=DataClass.RESTRICTED),
    "restricted with clearance": _case(
        data_classification=DataClass.RESTRICTED,
        subject={**ALICE, "groups": ["/acme/restricted-readers"]},
    ),
    "restricted via role": _case(
        data_classification=DataClass.RESTRICTED, subject={**ALICE, "roles": ["restricted-readers"]}
    ),
    "unknown side effect": _case(side_effect=None),
    "low-risk write": _case(side_effect=SideEffect.WRITE, ctx={"risk_tier": RiskTier.LOW}),
    "external write medium": _case(side_effect=SideEffect.WRITE, tool_tags=("external",)),
    "internal write medium": _case(side_effect=SideEffect.WRITE),
    "write high": _case(side_effect=SideEffect.WRITE, ctx={"risk_tier": RiskTier.HIGH}),
    "irreversible medium": _case(side_effect=SideEffect.IRREVERSIBLE),
    "irreversible high": _case(
        side_effect=SideEffect.IRREVERSIBLE, ctx={"risk_tier": RiskTier.HIGH}
    ),
    "payment within limit": _case(
        tool="erp__post_payment", side_effect=SideEffect.IRREVERSIBLE, arguments={"amount_aud": 900}
    ),
    "payment over medium limit": _case(
        tool="erp__post_payment",
        side_effect=SideEffect.IRREVERSIBLE,
        arguments={"amount_aud": 20000},
    ),
    "payment approved and bound": _case(
        tool="erp__post_payment",
        side_effect=SideEffect.IRREVERSIBLE,
        arguments={"amount_aud": 528},
        approval=APPROVED,
    ),
    "payment approval not bound": _case(
        tool="erp__post_payment",
        side_effect=SideEffect.IRREVERSIBLE,
        arguments={"amount_aud": 528},
        approval={**APPROVED, "bound": False},
    ),
    "payment approved by the requester": _case(
        tool="erp__post_payment",
        side_effect=SideEffect.IRREVERSIBLE,
        arguments={"amount_aud": 528},
        approval={**APPROVED, "approver": CTX.user_id},
    ),
    "irreversible high approved by one person": _case(
        side_effect=SideEffect.IRREVERSIBLE, ctx={"risk_tier": RiskTier.HIGH}, approval=APPROVED
    ),
    "payment over medium limit in high tier": _case(
        tool="erp__post_payment",
        side_effect=SideEffect.IRREVERSIBLE,
        arguments={"amount_aud": 20000},
        ctx={"risk_tier": RiskTier.HIGH},
    ),
}


@pytest.mark.integration
@pytest.mark.parametrize("name", list(PARITY))
async def test_cerbos_and_opa_agree(name: str) -> None:
    """The alternative must decide exactly like the default: same effect, same approvers."""
    request = PARITY[name]
    opa = await OpaPolicyDecisionPoint(
        os.environ.get("P0_OPA_URL", "http://127.0.0.1:8181")
    ).decide(request)
    cerbos = await CerbosPolicyDecisionPoint(CERBOS).decide(request)
    assert (cerbos.effect, cerbos.approvers) == (opa.effect, opa.approvers), (opa, cerbos)
