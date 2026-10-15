"""Approval routing (Step 28): the in-process rules give the same route as the OPA policy, and
the OPA client falls back to a safe route when the policy engine is down."""

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from agent_contracts.models import RiskTier, RunContext, SideEffect
from agent_tools.approvals import OpaApprovalRouter, RulesApprovalRouter

ROOT = Path(__file__).resolve().parents[3]
CTX = RunContext(tenant_id="acme", user_id="alice", agent_id="helpdesk")
CASES = [
    ("send_email", SideEffect.WRITE, RiskTier.MEDIUM, None),
    ("send_email", SideEffect.WRITE, RiskTier.LOW, "group:support-leads"),
    ("erp__post_payment", SideEffect.IRREVERSIBLE, RiskTier.MEDIUM, "group:support-leads"),
    ("erp__post_payment", SideEffect.IRREVERSIBLE, RiskTier.HIGH, None),
    ("delete_record", SideEffect.IRREVERSIBLE, RiskTier.HIGH, "group:ops"),
]


def opa_route(tool: str, side: SideEffect, risk: RiskTier, requested: str | None) -> dict:
    payload = {
        "tool": tool,
        "side_effect": side.value,
        "risk_tier": risk.value,
        "requested_approvers": requested,
        "ctx": CTX.model_dump(mode="json"),
    }
    out = subprocess.run(
        [
            "opa",
            "eval",
            "-f",
            "json",
            "-d",
            str(ROOT / "policies"),
            "--stdin-input",
            "data.agent.approvals.route",
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)["result"][0]["expressions"][0]["value"]


@pytest.mark.skipif(shutil.which("opa") is None, reason="opa CLI not installed")
@pytest.mark.parametrize(("tool", "side", "risk", "requested"), CASES)
async def test_rules_router_matches_the_opa_policy(
    tool: str, side: SideEffect, risk: RiskTier, requested: str | None
) -> None:
    rules = await RulesApprovalRouter().route(tool, side, risk, requested, CTX)
    policy = opa_route(tool, side, risk, requested)
    assert (rules.approvers, rules.two_person, rules.sla_minutes, rules.escalate_to) == (
        policy["approvers"],
        policy["two_person"],
        policy["sla_minutes"],
        policy["escalate_to"],
    )
    assert list(rules.reasons) == policy["reasons"]


async def test_opa_router_reads_the_route_and_falls_back_when_down() -> None:
    def ok(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/data/agent/approvals/route"
        return httpx.Response(
            200,
            json={
                "result": {
                    "approvers": "group:finance-approvers",
                    "two_person": False,
                    "sla_minutes": 60,
                    "escalate_to": "group:approval-leads",
                    "reasons": ["x"],
                    "version": "v1",
                }
            },
        )

    route = await OpaApprovalRouter(transport=httpx.MockTransport(ok)).route(
        "erp__post_payment", SideEffect.IRREVERSIBLE, RiskTier.MEDIUM, None, CTX
    )
    assert route.approvers == "group:finance-approvers"
    assert route.policy_version == "v1"
    down = OpaApprovalRouter(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    fallback = await down.route("x", SideEffect.WRITE, RiskTier.LOW, "group:ops", CTX)
    assert fallback.approvers == "group:ops"  # still paused, never auto-approved
    assert fallback.sla_minutes == 30
