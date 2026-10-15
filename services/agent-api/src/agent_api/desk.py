"""The approval desk (Step 28): every approval a run waits for, routed, timed and audited.

When a run pauses for an approval, the desk asks policy for the route (approver group, SLA,
escalation group, two-person rule), files an ``ApprovalRequest`` with the gate, and annotates
the interrupt so reviewers see the proposed action, the reason, the data and the risk.

Decisions:

* **approve** — optionally with **edited arguments**; the call then runs with exactly those, and
  the approval evidence is bound to them, so policy checks what will actually run. Under the
  two-person rule the first approval is recorded and the run waits for a second, different
  approver.
* **reject** — the run continues on its rejection path; nothing is executed.
* **escalate** — the request moves to the escalation group with a fresh SLA.

When the SLA runs out, the request is escalated once (if policy names an escalation group);
when that runs out too, the run is **cancelled safely**: nothing behind the approval ran.
Every step is audited (``approval.requested | partial | decided | escalated | expired``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_contracts.interfaces import ApprovalGate, AuditSink
from agent_contracts.models import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    RiskTier,
    RunContext,
    SideEffect,
    ToolSpec,
)
from agent_runtime.approvals import in_group
from agent_tools.approvals import ApprovalRouter

Clock = Callable[[], datetime]


class ApprovalExpiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class Verdict:
    """What the desk decided: ``resume`` with a decision payload, or keep waiting."""

    resume: dict[str, Any] | None
    waiting: str | None = None  # "second_approver" | "escalated"


def _utc() -> datetime:
    return datetime.now(UTC)


class ApprovalDesk:
    def __init__(
        self,
        gate: ApprovalGate,
        router: ApprovalRouter,
        audit: AuditSink,
        clock: Clock = _utc,
    ) -> None:
        self.gate, self.router, self.audit, self.clock = gate, router, audit, clock

    # ------------------------------------------------------------ opening

    async def open(
        self,
        items: Sequence[dict[str, Any]],
        ctx: RunContext,
        risk: RiskTier,
        specs: Mapping[str, ToolSpec],
    ) -> None:
        """Route and file every approval interrupt not yet filed (annotates ``items``)."""
        for item in items:
            if item.get("reason") != "approval" or item.get("approval_id"):
                continue
            calls = self.calls_of(item)
            tool = calls[0]["tool"] if calls else "?"
            spec = specs.get(tool)
            side = spec.side_effect if spec else SideEffect.IRREVERSIBLE  # unknown: strictest
            route = await self.router.route(tool, side, risk, item.get("approvers"), ctx)
            expires = self.clock() + timedelta(minutes=route.sla_minutes)
            record = await self.gate.request(
                ApprovalRequest(
                    ctx=ctx,
                    tool=tool,
                    arguments=dict(calls[0].get("arguments") or {}) if calls else {},
                    reason=str(item.get("message") or ""),
                    risk=risk,
                    approvers=route.approvers,
                    expires_at=expires,
                )
            )
            item.update(
                approval_id=record.request.id,
                approvers=route.approvers,
                two_person=route.two_person,
                sla_minutes=route.sla_minutes,
                escalate_to=route.escalate_to,
                escalated=False,
                expires_at=expires.isoformat(timespec="seconds"),
                risk=risk.value,
                side_effect=side.value,
                routing=list(route.reasons),
                approvals=[],
            )
            await self._audit("approval.requested", ctx, item)

    @staticmethod
    def calls_of(item: Mapping[str, Any]) -> list[dict[str, Any]]:
        action = item.get("action") or {}
        if "calls" in action:
            return [dict(c) for c in action["calls"]]
        return (
            [{"tool": action.get("tool"), "arguments": action.get("arguments")}] if action else []
        )

    # ------------------------------------------------------------ deciding

    async def _record(self, item: Mapping[str, Any], ctx: RunContext) -> ApprovalRecord:
        """The gate's record; re-filed from the run's own copy if the gate lost it (an
        in-memory gate after a restart). Same id, same expiry: a restart extends nothing."""
        try:
            return await self.gate.get(str(item["approval_id"]))
        except KeyError:
            calls = self.calls_of(item)
            await self.gate.request(
                ApprovalRequest(
                    id=str(item["approval_id"]),
                    ctx=ctx,
                    tool=calls[0]["tool"] if calls else "?",
                    arguments=dict(calls[0].get("arguments") or {}) if calls else {},
                    reason=str(item.get("message") or ""),
                    risk=RiskTier(item.get("risk") or "high"),
                    approvers=str(item.get("approvers") or "group:approvers"),
                    expires_at=datetime.fromisoformat(str(item["expires_at"])),
                )
            )
            return await self.gate.get(str(item["approval_id"]))

    async def decide(
        self,
        item: dict[str, Any],
        ctx: RunContext,
        who: Any,
        action: str,
        arguments: dict[str, Any] | None = None,
        comment: str = "",
    ) -> Verdict:
        record = await self._record(item, ctx)
        if record.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(f"approval {item['approval_id']} expired")
        if who.tenant != ctx.tenant_id:
            raise PermissionError("approvers must belong to the run's tenant")
        if who.user == ctx.user_id:
            raise PermissionError("the requester cannot approve their own run")
        if not in_group(tuple(who.groups), item.get("approvers")):
            raise PermissionError(f"approver is not in {item.get('approvers')}")
        if action not in ("approve", "reject", "escalate"):
            raise ValueError("decision must be approve, reject or escalate")
        if arguments is not None and (action != "approve" or len(self.calls_of(item)) != 1):
            raise ValueError("arguments can only be edited when approving a single action")
        if action == "escalate":
            await self._escalate(item, ctx, by=who.user, reason=comment or "escalated by reviewer")
            return Verdict(None, "escalated")
        if action == "approve" and item.get("two_person") and not item["approvals"]:
            if arguments is not None:
                raise ValueError("under two-person approval, the second approver may edit")
            item["approvals"].append({"by": who.user, "groups": list(who.groups)})
            await self._audit("approval.partial", ctx, item, by=who.user)
            return Verdict(None, "second_approver")
        first = item["approvals"][0] if item.get("approvals") else None
        if first and first["by"] == who.user:
            raise PermissionError("two-person approval needs a second, different approver")
        decision = (
            ApprovalDecision(
                status=ApprovalStatus.APPROVED,
                decided_by=who.user,
                edited_arguments=arguments,
                comment=comment,
            )
            if action == "approve"
            else ApprovalDecision(
                status=ApprovalStatus.REJECTED, decided_by=who.user, comment=comment
            )
        )
        await self.gate.decide(str(item["approval_id"]), decision)
        payload: dict[str, Any] = {
            "approved": action == "approve",
            "by": first["by"] if first else who.user,
            "groups": first["groups"] if first else list(who.groups),
            "at": self.clock().isoformat(timespec="seconds"),
            "comment": comment,
            "approvers": item.get("approvers"),
        }
        if first:
            payload |= {"co_by": who.user, "co_groups": list(who.groups)}
        if arguments is not None:
            payload["arguments"] = arguments
        await self._audit(
            "approval.decided",
            ctx,
            item,
            by=who.user,
            decision=action,
            edited=arguments is not None,
        )
        return Verdict(payload)

    async def _escalate(self, item: dict[str, Any], ctx: RunContext, by: str, reason: str) -> None:
        target = item.get("escalate_to")
        if not target:
            raise ValueError("this approval has no escalation group")
        record = await self._record(item, ctx)
        if record.status is ApprovalStatus.PENDING:
            await self.gate.decide(
                str(item["approval_id"]),
                ApprovalDecision(status=ApprovalStatus.ESCALATED, decided_by=by, comment=reason),
            )
        expires = self.clock() + timedelta(minutes=int(item.get("sla_minutes") or 60))
        new = await self.gate.request(
            record.request.model_copy(
                update={
                    "id": f"{record.request.id}-esc",
                    "approvers": target,
                    "expires_at": expires,
                    "created_at": self.clock(),
                }
            )
        )
        item.update(
            approval_id=new.request.id,
            approvers=target,
            escalated=True,
            escalate_to=None,  # once
            expires_at=expires.isoformat(timespec="seconds"),
            approvals=[],
        )
        await self._audit("approval.escalated", ctx, item, by=by, why=reason)

    # ------------------------------------------------------------ timers

    async def overdue(self, item: dict[str, Any], ctx: RunContext) -> str:
        """An SLA ran out: ``escalated`` (first time, if policy allows) or ``expired``."""
        if item.get("escalate_to") and not item.get("escalated"):
            await self._escalate(item, ctx, by="sla-timer", reason="no decision within the SLA")
            return "escalated"
        await self._audit("approval.expired", ctx, item)
        return "expired"

    def is_overdue(self, item: Mapping[str, Any]) -> bool:
        exp = item.get("expires_at")
        return bool(exp) and datetime.fromisoformat(str(exp)) <= self.clock()

    async def _audit(
        self, kind: str, ctx: RunContext, item: Mapping[str, Any], **extra: Any
    ) -> None:
        await self.audit.append(
            AuditEvent(
                type=kind,
                ctx=ctx,
                payload={
                    "approval_id": item.get("approval_id"),
                    "node": item.get("node"),
                    "approvers": item.get("approvers"),
                    "risk": item.get("risk"),
                    "tools": [c["tool"] for c in self.calls_of(item)],
                    **extra,
                },
            )
        )
