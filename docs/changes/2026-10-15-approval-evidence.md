# A human approval now travels with the action it approves

**Step:** 15 · **Commit:** feat(agent-tools): bind approvals to the exact tool call and let policy accept them · **Date:** 2026-10-15

## What changed
- When a person approves a paused action, the decision (who, their groups, approved or not)
  becomes **evidence attached to that one call**: the tool's name and a fingerprint (SHA-256) of
  its exact arguments. This works for tool steps in a workflow and for calls an agent proposed.
- The platform checks the fingerprint before policy sees the call, so an approval for paying
  AUD 528 cannot be reused to pay AUD 99,999, or to call a different tool.
- The approver must be in the group the spec names (for example `group:finance-approvers`);
  otherwise the approval does not count and the action does not run.
- The OPA policy (version `2026-10-15.2`), the Cerbos alternative and the in-process rules all
  accept an approval only if it is approved, bound to the call, from someone other than the
  person who asked, and from the policy's approver group. Denials still win (an approved payment
  over the limit is still denied). High-risk two-person approval still cannot be met by one
  person.
- The audit record of the call names the approval and the approver.

## Why
Irreversible actions always needed an approval, but the decision never reached policy, so they
could never run: the platform failed closed, as designed, until this path existed. Step 15's
reference app pays an invoice, so it needs this now. Binding the decision to the exact
arguments makes it impossible to approve one thing and run another.

## How to check it
`just policy-test` (OPA 28 tests including 7 new approval cases; Cerbos 12 including 2 new) and
`uv run pytest libs/agent-tools/tests/test_authorising.py libs/agent-runtime/tests/test_executors.py libs/agent-compiler/tests/test_react_agent.py`.

## Risks and follow-ups
The approver's identity and groups come from whoever resumes the run; today that is in-process
code. The run API (Step 17) must take them from the approver's verified token. Routing approvals
to groups, SLA timers, edit-and-approve, escalation, expiry and two-person approval are Step 28.
