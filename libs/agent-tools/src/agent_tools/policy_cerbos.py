"""``policy_decision_point.cerbos``: the same tool-call rules, evaluated by Cerbos (Step 10).

Cerbos answers allow or deny per action, so a three-way decision is asked as two actions on the
resource ``tool`` (``policies/cerbos/tool.yaml``):

* ``call`` denied → ``deny``;
* ``call`` allowed, ``call_without_approval`` denied → ``require_approval``;
* both allowed → ``allow``.

Reasons and approvers come back as rule outputs. Every attribute is sent on every request, nulls
included, so no rule can be skipped because an attribute is missing. Fails closed like the OPA
client: unreachable, an error status or an unexpected answer means ``deny``.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from agent_contracts.interfaces import PolicyDecisionPoint
from agent_contracts.models import PolicyDecision, PolicyEffect, PolicyInput

CALL, NO_APPROVAL = "call", "call_without_approval"
ALLOW = "EFFECT_ALLOW"


NO_APPROVAL_EVIDENCE: dict[str, Any] = {
    "approval_id": "",
    "approved": False,
    "approver": "",
    "approver_groups": [],
    "bound": False,
}


def cerbos_request(request: PolicyInput) -> dict[str, Any]:
    subject = request.subject
    ctx = request.ctx
    data = request.model_dump(mode="json")
    principal = {
        "id": str(subject.get("user") or "anonymous"),
        "roles": ["user", *[str(r) for r in subject.get("roles", [])]],
        "attr": {
            "authenticated": subject.get("authenticated") is True,
            "groups": [str(g) for g in subject.get("groups", [])],
            "roles": [str(r) for r in subject.get("roles", [])],
        },
    }
    resource = {
        "kind": "tool",
        "id": request.tool or "unknown",
        "attr": {
            "tenant_id": ctx.tenant_id,
            "agent_id": ctx.agent_id,
            "risk_tier": ctx.risk_tier.value,
            # absent values become "" so the rules still evaluate (and an unknown side effect denies)
            "side_effect": data["side_effect"] or "",
            "data_classification": data["data_classification"] or "",
            "resource_tenant_id": request.resource_tenant_id,
            "allowed_tools": list(request.allowed_tools)
            if request.allowed_tools is not None
            else None,
            "tool_ref": request.tool_ref,
            "tool_tags": list(request.tool_tags),
            "arguments": data["arguments"],
            "user_id": ctx.user_id,
            "approval": {**NO_APPROVAL_EVIDENCE, **(request.approval or {})},
        },
    }
    return {
        "requestId": uuid.uuid4().hex,
        "principal": principal,
        "resources": [{"actions": [CALL, NO_APPROVAL], "resource": resource}],
        "includeMeta": True,
    }


def decision_from(result: dict[str, Any]) -> PolicyDecision:
    actions = result.get("actions") or {}
    outputs = [o.get("val") for o in result.get("outputs") or [] if isinstance(o.get("val"), dict)]
    meta = result.get("meta") or {}
    matched = (meta.get("actions") or {}).get(CALL, {}).get("matchedPolicy")
    version = f"cerbos:{matched}" if matched else "cerbos"
    if actions.get(CALL) != ALLOW:
        reasons = sorted({str(o["deny"]) for o in outputs if "deny" in o})
        return PolicyDecision(
            effect=PolicyEffect.DENY,
            reasons=tuple(reasons or ["no rule allows this call"]),
            policy_version=version,
        )
    if actions.get(NO_APPROVAL) != ALLOW:
        approvals = [o for o in outputs if "approval" in o]
        approvers = sorted({str(o.get("approvers")) for o in approvals if o.get("approvers")})
        two_person = "group:two-person-approvers"
        return PolicyDecision(
            effect=PolicyEffect.REQUIRE_APPROVAL,
            reasons=tuple(sorted({str(o["approval"]) for o in approvals}) or ["approval required"]),
            approvers=two_person
            if two_person in approvers
            else (approvers[0] if approvers else "group:approvers"),
            policy_version=version,
        )
    allowed = [str(o["reason"]) for o in outputs if "reason" in o]
    return PolicyDecision(
        effect=PolicyEffect.ALLOW, reasons=tuple(allowed[:1]), policy_version=version
    )


class CerbosPolicyDecisionPoint(PolicyDecisionPoint):
    """Cerbos PDP over its HTTP API (``POST /api/check/resources``)."""

    def __init__(
        self,
        url: str = "http://127.0.0.1:3592",
        timeout: float = 2.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def decide(self, request: PolicyInput) -> PolicyDecision:
        try:
            resp = await self._client.post("/api/check/resources", json=cerbos_request(request))
            resp.raise_for_status()
            results = resp.json().get("results")
            if not isinstance(results, list) or len(results) != 1:
                return PolicyDecision(
                    effect=PolicyEffect.DENY, reasons=("unexpected answer from Cerbos",)
                )
            return decision_from(results[0])
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                reasons=(f"policy engine unavailable: {type(exc).__name__}",),
            )

    async def aclose(self) -> None:
        await self._client.aclose()
