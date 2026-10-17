"""Approval routing (Step 28): OPA decides who approves a paused action, the SLA, and where it
escalates (``data.agent.approvals.route``, policies/agent/approvals/route.rego).

``RulesApprovalRouter`` applies the same rules in process from the same policy data
(``policies/agent/config/data.json``), for tests and development without OPA; a policy test and
a unit test keep the two in step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import httpx
from agent_contracts.models import ApprovalRoute, RiskTier, RunContext, SideEffect

# The platform's policy data: in the repository, or the copy built into the wheel for agent
# projects that install the platform from git (Step 39, pyproject force-include).
_REPO_DATA = Path(__file__).resolve().parents[4] / "policies" / "agent" / "config" / "data.json"
POLICY_DATA = (
    _REPO_DATA
    if _REPO_DATA.is_file()
    else Path(__file__).resolve().parent / "_platform" / "policy-data.json"
)


class ApprovalRouter(Protocol):
    async def route(
        self,
        tool: str,
        side_effect: SideEffect,
        risk_tier: RiskTier,
        requested_approvers: str | None,
        ctx: RunContext,
    ) -> ApprovalRoute: ...


class OpaApprovalRouter:
    def __init__(
        self,
        url: str = "http://127.0.0.1:8181",
        path: str = "agent/approvals/route",
        timeout: float = 2.0,
        transport: httpx.AsyncBaseTransport | None = None,
        fallback_group: str = "group:approvers",
    ) -> None:
        self.path = path.strip("/")
        self.fallback_group = fallback_group
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def route(
        self,
        tool: str,
        side_effect: SideEffect,
        risk_tier: RiskTier,
        requested_approvers: str | None,
        ctx: RunContext,
    ) -> ApprovalRoute:
        body = {
            "input": {
                "tool": tool,
                "side_effect": side_effect.value,
                "risk_tier": risk_tier.value,
                "requested_approvers": requested_approvers,
                "ctx": ctx.model_dump(mode="json"),
            }
        }
        try:
            resp = await self._client.post(f"/v1/data/{self.path}", json=body)
            resp.raise_for_status()
            r = resp.json().get("result")
            if not isinstance(r, dict):
                raise ValueError("approval routing policy is not loaded")
            return ApprovalRoute(
                approvers=str(r["approvers"]),
                two_person=bool(r.get("two_person")),
                sla_minutes=int(r.get("sla_minutes") or 60),
                escalate_to=r.get("escalate_to"),
                reasons=tuple(str(x) for x in r.get("reasons", ())),
                policy_version=r.get("version"),
            )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # still pause: route to the strictest available group with a short SLA
            return ApprovalRoute(
                approvers=requested_approvers or self.fallback_group,
                sla_minutes=30,
                reasons=(f"routing policy unavailable ({type(exc).__name__}); default route",),
            )


class RulesApprovalRouter:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.cfg = (data or json.loads(POLICY_DATA.read_text()))["approvals"]

    async def route(
        self,
        tool: str,
        side_effect: SideEffect,
        risk_tier: RiskTier,
        requested_approvers: str | None,
        ctx: RunContext,
    ) -> ApprovalRoute:
        cfg = self.cfg
        two_person = risk_tier is RiskTier.HIGH and side_effect is SideEffect.IRREVERSIBLE
        override = (cfg.get("tools", {}).get(tool) or {}).get("approvers")
        if two_person:
            approvers = cfg["two_person_group"]
        elif override:
            approvers = override
        elif requested_approvers:
            approvers = requested_approvers
        else:
            approvers = cfg["default_group"]
        sla = int(cfg.get("sla_minutes", {}).get(risk_tier.value, 60))
        reasons = [f"decide within {sla} minutes ({risk_tier.value} risk)"]
        if two_person:
            reasons.append("high-risk irreversible action: two people must approve")
        elif override:
            reasons.append(f"tool {tool} is approved by {override}")
        return ApprovalRoute(
            approvers=approvers,
            two_person=two_person,
            sla_minutes=sla,
            escalate_to=cfg.get("escalate_to"),
            reasons=tuple(sorted(reasons)),
            policy_version="rules",
        )
