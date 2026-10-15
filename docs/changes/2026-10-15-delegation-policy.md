# OPA decides delegation between agents

**Step:** 22 · **Commit:** feat(policies): authorise delegation between agents · **Date:** 2026-10-15

## What changed
`policies/agent/tools/decision.rego` (version 2026-10-15.3) also decides `action:
"agent.delegate"`. The worker is the `tool`, and the team's workers are its `allowed_tools`.
- The existing rules apply: authentication, tenant kill switch, the supervisor's own kill
  switch, and "not granted" for a worker outside the team.
- New rule: delegation to a worker whose agent kill switch is on is denied.
- Three new Rego tests: a team worker is allowed; a worker outside the team is denied; a
  switched-off worker is denied.

## Why
OWASP ASI07 lists "OPA on delegation". The supervisor asks policy before every hand-off, so
agents can be switched off or kept out of a team centrally, without changing the spec.

## How to check it
`just policy-test`: 31/31 pass.

## Risks and follow-ups
The Cerbos alternative covers tool calls only. With Cerbos as the PDP, delegation decisions
need a Cerbos `agent` resource policy (follow-up).
