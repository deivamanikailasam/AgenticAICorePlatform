# Approval desk: routed, timed approvals with edits, escalation, expiry and two-person rule

**Step:** 28 · **Commit:** feat(agent-api): approval desk with routing, edits, escalation, expiry and two people · **Date:** 2026-10-15

## What changed
- `agent_api.desk.ApprovalDesk`:
  - When a run pauses, the desk routes the approval by policy (approver group, SLA, escalation
    group, two-person rule) and files an `ApprovalRequest` with the gate.
  - It annotates the interrupt with the proposed action, reason, data, risk, side effect,
    expiry and routing reasons.
  - Every step is audited (`approval.requested`, `partial`, `decided`, `escalated`, `expired`).
- Decisions (`POST /v1/runs/{id}/approvals`, with `{"decision": "approve" | "reject" |
  "escalate", "arguments": {...}, "comment": ...}`; the old `{"approved": bool}` still works):
  - **Edited arguments:** the call runs with exactly those, and the approval evidence is bound to
    them (tool nodes and agent nodes).
  - **Escalate:** the request moves to the escalation group with a fresh SLA.
  - **Two-person:** the first approval is recorded and the run waits for a second, different
    approver.
- **SLA timers:** `RunManager.sweep_approvals()` runs every 30 s in the API. An overdue approval
  escalates once, then expires, and the run is cancelled with `stop_reason: "approval expired"`.
  Nothing behind the approval runs. Deciding an expired approval returns 409.
- **Reviewer inbox:** `GET /v1/approvals` lists approvals the caller may decide: same tenant, in
  the routed group, not their own run.
- **An irreversible tool always waits:** the assembly refuses to build a plan in which an
  irreversible tool (tool node or agent node) can run without an approval step.
- **Policy:**
  - Tool decisions use the tool's own approver group from the approvals data. A payment now
    needs `finance-approvers`; a general approver is no longer enough.
  - Two-person approval can now be satisfied by two different people from the two-person group,
    neither of them the requester. Before this change it could never be satisfied.
  - The rules PDP and the Cerbos policy mirror both changes.
- Approval evidence gains `co_approver`, `co_approver_groups` and `edited`. A decision carries
  the routed group, so escalations and tool overrides are honoured.

## Why
Step 28's "Done when":
- an irreversible tool always waits for approval;
- an edited approval runs with the edited arguments;
- an expired approval cancels safely.

## How to check it
- `uv run pytest services/agent-api/tests/test_hitl.py` (7 tests):
  - an unapproved irreversible tool is refused (422);
  - the inbox shows the action, reason, data, risk, group and the 60-minute expiry;
  - an edited approval pays AUD 40 instead of 42;
  - the SLA escalates, then expires, the run is cancelled and nothing is paid;
  - a reviewer escalates, and only the escalation group may then decide;
  - a rejection runs nothing;
  - two different approvers are needed for a high-risk payment.
- `just policy-test`: 45 rego tests and 12 Cerbos tests.
- With the stand-in OPA and Cerbos: the Cerbos/OPA parity tests pass, including the new
  two-person and tool-group cases.

## Risks and follow-ups
- The approval gate is in memory. A restart re-files approvals from the run record with the same
  id and expiry. A durable Postgres gate is the next commit.
