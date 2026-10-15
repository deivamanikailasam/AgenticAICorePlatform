# Approval routing in OPA: approvers, SLA and escalation

**Step:** 28 · **Commit:** feat(policies): route approvals by policy with SLA and escalation · **Date:** 2026-10-15

## What changed
- New OPA rule `data.agent.approvals.route` (`policies/agent/approvals/route.rego`, version
  2026-10-15.1). For a paused action it returns:
  - the approver group, chosen in this order: the two-person group for high-risk irreversible
    actions, then the tool's own approvers, then the spec's group, then the default;
  - whether two people must approve;
  - the SLA in minutes for the risk tier;
  - the escalation group and the reasons.
- Policy data gains `approvals` (`policies/agent/config/data.json`):
  - default group `group:approvers`, two-person group `group:two-person-approvers`;
  - escalation to `group:approval-leads`;
  - SLAs of 240, 60 and 30 minutes for low, medium and high risk;
  - `erp__post_payment` is approved by `group:finance-approvers`.

  These are configuration values to tune, not measurements.
- `ApprovalRoute` model. `OpaApprovalRouter`: if OPA is unreachable, the action still pauses
  for the spec's group (or the default) with a 30-minute SLA; it is never auto-approved.
  `RulesApprovalRouter` applies the same rules in process from the same data file.

## Why
Step 28 requires approvals "routed by OPA to the right group with SLA timers". The next commits
use the route when a run pauses.

## How to check it
- `just policy-test`: 5 new rego tests, 40/40 in total.
- `uv run pytest libs/agent-tools/tests/test_approval_routing.py`: the rules router matches
  `opa eval` on 5 cases, and the OPA fallback is checked.

## Risks and follow-ups
- Two-person approval is routed here. Collecting the second decision is part of the approval
  desk (next commit).
