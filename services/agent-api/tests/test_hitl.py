"""Human-in-the-loop (Step 28 "Done when"): an irreversible tool always waits for approval; an
edited approval runs with the edited arguments; an expired approval cancels safely. Also:
routing by policy, the reviewer inbox, escalation and two-person approval."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from agent_api.app import build_app
from agent_api.assembly import Assembly
from agent_api.desk import ApprovalDesk
from agent_api.runs import RunManager
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import InMemoryApprovalGate, RulesPolicyDecisionPoint
from agent_contracts.models import Message, Role, SideEffect
from agent_spec import load_spec_text
from agent_spec.platform import load_platform
from agent_tools.approvals import RulesApprovalRouter
from agent_tools.builtin import CalculatorTool
from agent_tools.registry import InMemoryToolRegistry
from mcp.server.auth.provider import AccessToken

USERS = {
    "t-alice": ("alice", ["/acme/finance"]),
    "t-jsmith": ("j.smith", ["/acme/approvers", "/acme/finance-approvers"]),
    "t-carol": ("carol", ["/acme/finance"]),
    "t-lead": ("lee", ["/acme/approval-leads", "/acme/approvers"]),
    "t-bob": ("bob", ["/acme/two-person-approvers"]),
    "t-dana": ("dana", ["/acme/two-person-approvers"]),
}

SPEC = """
spec_version: 1
name: pay-total
version: 0.1.0
owner: finance-platform
description: Adds up an amount and pays it after approval.
risk_tier: RISK
autonomy: workflow
pattern: workflow
runtime: { adapter: langgraph, durability: memory }
budgets: { max_steps: 10, max_wall_seconds: 30, max_tokens: 5000, max_cost_aud: 0.5 }
tools:
  - ref: local://platform/calculator
  - ref: local://test/pay
APPROVAL
graph:
  nodes:
    - { id: total, type: tool, tool: calculator, config: { args: { expression: $input.expression } } }
    - { id: pay, type: tool, tool: pay, config: { args: { amount_aud: $total.result } } }
  edges: ["total -> pay: on_ok"]
"""
GATED = '    approval: { required: true, approvers: "group:finance-approvers" }'


class Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token not in USERS:
            return None
        user, groups = USERS[token]
        return AccessToken(
            token=token,
            client_id="t",
            scopes=[],
            expires_at=None,
            claims={"preferred_username": user, "groups": groups},
        )


class Pay(CalculatorTool):
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    @property
    def spec(self) -> Any:
        return super().spec.model_copy(
            update={
                "name": "pay",
                "ref": "local://test/pay",
                "side_effect": SideEffect.IRREVERSIBLE,
                "idempotent": False,
                "input_schema": {
                    "type": "object",
                    "properties": {"amount_aud": {"type": "number"}},
                    "required": ["amount_aud"],
                },
                "output_schema": None,
            }
        )

    async def invoke(self, arguments: Any, ctx: Any) -> Any:
        self.calls.append(dict(arguments))
        return {"paid": arguments["amount_aud"]}


class Clock:
    def __init__(self) -> None:
        # starts at the real time: the gate also checks expiry against the wall clock
        self.now = datetime.now(UTC).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.now


class World:
    def __init__(self, risk: str = "medium", gated: bool = True) -> None:
        self.payments: list[dict[str, Any]] = []
        self.audit = MemoryAuditSink()
        self.clock = Clock()

        async def registry(spec: Any) -> InMemoryToolRegistry:
            reg = InMemoryToolRegistry()
            await reg.register(CalculatorTool())
            await reg.register(Pay(self.payments))
            return reg

        async def no_memory(spec: Any) -> None:
            return None

        assembly = Assembly(
            load_platform(),
            RulesPolicyDecisionPoint(),
            self.audit,
            model_factory=lambda s: ScriptedModelClient(
                [Message(role=Role.ASSISTANT, content="ok")]
            ),
            registry_factory=registry,
            memory_factory=no_memory,
        )
        text = SPEC.replace("RISK", risk).replace("APPROVAL", GATED if gated else "")
        if risk == "high":
            text += "evals: { suite: hitl }\n"
        spec = load_spec_text(text)
        desk = ApprovalDesk(
            InMemoryApprovalGate(), RulesApprovalRouter(), self.audit, clock=self.clock
        )
        self.manager = RunManager({spec.name: spec}, assembly, desk=desk)
        self.app = build_app(self.manager, Verifier())

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://t")

    async def start(self, c: httpx.AsyncClient, expression: str = "40+2") -> Any:
        resp = await c.post(
            "/v1/agents/pay-total/runs",
            json={"input": {"expression": expression}},
            headers=auth("t-alice"),
        )
        assert resp.status_code == 201, resp.text
        run = self.manager.runs[resp.json()["run_id"]]
        await self.manager.wait_idle(run)
        return run

    async def audited(self, kind: str) -> list[dict[str, Any]]:
        return [r.event.payload for r in await self.audit.read() if r.event.type == kind]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def decide(c: httpx.AsyncClient, run: Any, who: str, **body: Any) -> httpx.Response:
    return await c.post(f"/v1/runs/{run.run_id}/approvals", json=body, headers=auth(who))


async def test_an_irreversible_tool_without_an_approval_step_is_refused() -> None:
    w = World(gated=False)
    async with w.client() as c:
        resp = await c.post(
            "/v1/agents/pay-total/runs",
            json={"input": {"expression": "1+1"}},
            headers=auth("t-alice"),
        )
    assert resp.status_code == 422
    assert "tool pay is irreversible: the spec must require approval" in resp.json()["error"]
    assert w.payments == []


async def test_an_irreversible_tool_waits_and_reviewers_see_what_they_approve() -> None:
    w = World()
    async with w.client() as c:
        run = await w.start(c)
        assert run.status == "interrupted"
        assert w.payments == [], "nothing is paid while the approval is pending"
        inbox = (await c.get("/v1/approvals", headers=auth("t-jsmith"))).json()["approvals"]
        mine = (await c.get("/v1/approvals", headers=auth("t-alice"))).json()["approvals"]
        others = (await c.get("/v1/approvals", headers=auth("t-carol"))).json()["approvals"]
    assert mine == []  # the requester cannot approve their own run
    assert others == []  # not in the approver group
    (item,) = inbox
    assert item["proposed"] == {"tool": "pay", "arguments": {"amount_aud": 42.0}}
    assert item["reason"] == "Approve pay?"
    assert item["risk"] == "medium"
    assert item["side_effect"] == "irreversible"
    assert item["approvers"] == "group:finance-approvers"
    assert item["input"] == {"expression": "40+2"}
    expires = datetime.fromisoformat(item["expires_at"])
    assert expires - w.clock.now == timedelta(minutes=60)  # medium risk
    assert (await w.audited("approval.requested"))[0]["tools"] == ["pay"]


async def test_an_edited_approval_runs_with_the_edited_arguments() -> None:
    w = World()
    async with w.client() as c:
        run = await w.start(c)
        resp = await decide(
            c,
            run,
            "t-jsmith",
            decision="approve",
            arguments={"amount_aud": 40.0},
            comment="discount",
        )
        assert resp.status_code == 202, resp.text
        await w.manager.wait_idle(run)
    assert run.status == "succeeded"
    assert w.payments == [{"amount_aud": 40.0}], "exactly the edited arguments ran"
    (decided,) = await w.audited("approval.decided")
    assert decided["edited"] is True
    calls = await w.audited("tool.call")
    assert [c["tool"] for c in calls] == ["calculator", "pay"]


async def test_an_expired_approval_escalates_once_then_cancels_safely() -> None:
    w = World()
    async with w.client() as c:
        run = await w.start(c)
        w.clock.now += timedelta(minutes=61)
        assert await w.manager.sweep_approvals() == [(run.run_id, "escalated")]
        assert run.interrupts[0]["approvers"] == "group:approval-leads"
        w.clock.now += timedelta(minutes=61)
        assert await w.manager.sweep_approvals() == [(run.run_id, "expired")]
        late = await decide(c, run, "t-lead", decision="approve")
    assert run.status == "cancelled"
    assert w.payments == [], "nothing behind the approval ran"
    assert late.status_code == 404  # a cancelled run has nothing left to approve
    finished = [e for e in run.events if e["type"] == "RUN_FINISHED"][-1]
    assert finished["result"]["stop_reason"] == "approval expired"
    assert [e["approval_id"] for e in await w.audited("approval.expired")]


async def test_a_reviewer_escalates_and_only_the_escalation_group_may_then_decide() -> None:
    w = World()
    async with w.client() as c:
        run = await w.start(c)
        esc = await decide(c, run, "t-jsmith", decision="escalate", comment="unusual supplier")
        assert esc.json()["outcome"] == "escalated"
        # j.smith is no longer an approver of this run: it is not even visible to them
        assert (await decide(c, run, "t-jsmith", decision="approve")).status_code == 404
        assert (await decide(c, run, "t-lead", decision="approve")).status_code == 202
        await w.manager.wait_idle(run)
    assert w.payments == [{"amount_aud": 42.0}]
    assert (await w.audited("approval.escalated"))[0]["why"] == "unusual supplier"


async def test_a_rejection_runs_nothing() -> None:
    w = World()
    async with w.client() as c:
        run = await w.start(c)
        assert (await decide(c, run, "t-jsmith", approved=False)).status_code == 202
        await w.manager.wait_idle(run)
    assert w.payments == []


async def test_high_risk_irreversible_needs_two_different_approvers() -> None:
    w = World(risk="high")
    async with w.client() as c:
        run = await w.start(c)
        assert run.interrupts[0]["approvers"] == "group:two-person-approvers"
        expires = datetime.fromisoformat(run.interrupts[0]["expires_at"])
        assert expires - w.clock.now == timedelta(minutes=30)  # high risk
        first = await decide(c, run, "t-bob", decision="approve")
        assert first.json()["outcome"] == "second_approver"
        assert w.payments == []
        assert (await decide(c, run, "t-bob", decision="approve")).status_code == 403
        assert (await decide(c, run, "t-dana", decision="approve")).status_code == 202
        await w.manager.wait_idle(run)
    assert run.status == "succeeded"
    assert w.payments == [{"amount_aud": 42.0}]
    pay = next(c for c in await w.audited("tool.call") if c["tool"] == "pay")
    assert pay["approver"] == "bob"
