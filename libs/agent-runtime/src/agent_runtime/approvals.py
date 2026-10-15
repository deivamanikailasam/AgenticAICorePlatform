"""Approval decisions → evidence bound to one tool call (Step 15; edits and two-person approval:
Step 28).

An APPROVAL node's resume payload (``{"approved": true, "by": "j.smith", "groups": [...]}``,
supplied by the authenticated approval API) becomes ``ApprovalEvidence`` for the exact call that
follows: tool name plus a digest of its arguments. The spec's ``approvers`` group is checked
here; policy then checks its own rules (approved, bound, not the requester, the policy's group).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from agent_contracts.models import ApprovalEvidence, RunContext, arguments_digest


class ApproverNotAllowedError(PermissionError):
    pass


def in_group(groups: tuple[str, ...], required: str | None) -> bool:
    """``group:finance-approvers`` matches a Keycloak group path ending ``/finance-approvers``."""
    if not required:
        return True
    name = required.removeprefix("group:")
    return any(g == name or g.endswith(f"/{name}") for g in groups)


def evidence_for(
    decision: Mapping[str, Any] | None,
    *,
    approval_id: str,
    tool: str,
    arguments: Mapping[str, Any],
    required_group: str | None,
    ctx: RunContext,
) -> ApprovalEvidence | None:
    """Evidence for this call, or ``None`` when there is no decision. Raises when the approver is
    not in the group the spec names (the approval does not count)."""
    if not isinstance(decision, Mapping) or "approved" not in decision:
        return None
    approver = str(decision.get("by") or "")
    groups = tuple(str(g) for g in decision.get("groups", ()) or ())
    # the group policy routed the approval to (two-person, a tool's own approvers, an
    # escalation) replaces the spec's group; the approval desk already checked membership
    required_group = str(decision.get("approvers") or "") or required_group
    if decision.get("approved") and not in_group(groups, required_group):
        raise ApproverNotAllowedError(
            f"approval {approval_id}: {approver or 'unknown'} is not in {required_group}"
        )
    decided = decision.get("at")
    co = decision.get("co_by")
    return ApprovalEvidence(
        approval_id=approval_id,
        approved=bool(decision.get("approved")),
        approver=approver,
        approver_groups=groups,
        tool=tool,
        arguments_digest=arguments_digest(arguments),
        decided_at=datetime.fromisoformat(decided) if isinstance(decided, str) else None,
        co_approver=str(co) if co else None,
        co_approver_groups=tuple(str(g) for g in decision.get("co_groups", ()) or ()),
        edited="arguments" in decision,
    )


def edited_arguments(decision: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The arguments a reviewer approved instead of the proposed ones (Step 28), if any."""
    if (
        isinstance(decision, Mapping)
        and decision.get("approved")
        and isinstance(decision.get("arguments"), Mapping)
    ):
        return dict(decision["arguments"])
    return None
