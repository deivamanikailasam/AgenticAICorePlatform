import json

import httpx
import pytest
import respx
from agent_contracts.models import PolicyEffect, PolicyInput, RunContext, SideEffect
from agent_contracts.testing import PolicyDecisionPointContract
from agent_tools.policy import OpaPolicyDecisionPoint

CTX = RunContext(tenant_id="acme", user_id="alice", agent_id="a1")


@respx.mock
async def test_decision_is_read_from_opa() -> None:
    route = respx.post("http://opa/v1/data/agent/tools/decision").respond(
        200,
        json={
            "result": {"effect": "require_approval", "reasons": ["write"], "approvers": "group:fin"}
        },
    )
    d = await OpaPolicyDecisionPoint("http://opa").decide(
        PolicyInput(ctx=CTX, tool="t", side_effect=SideEffect.WRITE)
    )
    assert d.effect is PolicyEffect.REQUIRE_APPROVAL
    assert d.approvers == "group:fin"
    sent = json.loads(route.calls[0].request.content)["input"]
    assert sent["side_effect"] == "write"
    assert sent["ctx"]["tenant_id"] == "acme"


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"result": {"effect": "maybe"}}),
    ],
)
async def test_fails_closed(response: httpx.Response) -> None:
    respx.post("http://opa/v1/data/agent/tools/decision").mock(return_value=response)
    d = await OpaPolicyDecisionPoint("http://opa").decide(
        PolicyInput(ctx=CTX, tool="t", side_effect=SideEffect.READ)
    )
    assert d.effect is PolicyEffect.DENY


@respx.mock
async def test_unreachable_opa_denies() -> None:
    respx.post("http://opa/v1/data/agent/tools/decision").mock(
        side_effect=httpx.ConnectError("down")
    )
    d = await OpaPolicyDecisionPoint("http://opa").decide(PolicyInput(ctx=CTX, tool="t"))
    assert d.effect is PolicyEffect.DENY
    assert "unavailable" in d.reasons[0]


@pytest.mark.integration
class TestOpaContract(PolicyDecisionPointContract):
    """Against OPA with Project A's policies loaded (`just provision-policies`)."""

    def make(self) -> OpaPolicyDecisionPoint:
        import os

        return OpaPolicyDecisionPoint(os.environ.get("P0_OPA_URL", "http://127.0.0.1:8181"))


@pytest.mark.integration
async def test_live_policy_allows_authenticated_reads() -> None:
    import os

    pdp = OpaPolicyDecisionPoint(os.environ.get("P0_OPA_URL", "http://127.0.0.1:8181"))
    req = PolicyInput(
        ctx=CTX,
        tool="rag-core__search",
        side_effect=SideEffect.READ,
        subject={"authenticated": True},
    )
    assert (await pdp.decide(req)).effect is PolicyEffect.ALLOW
    anon = req.model_copy(update={"subject": {}})
    assert (await pdp.decide(anon)).reasons == ("not authenticated",)
