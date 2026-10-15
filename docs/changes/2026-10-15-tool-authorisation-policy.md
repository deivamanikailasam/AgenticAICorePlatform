# Decide every tool call from who, what, where and how risky

**Step:** 10 · **Commit:** feat(policy): decide tool calls by tenant, data class, arguments and risk tier · **Date:** 2026-10-15

## What changed
The OPA policy `agent.tools` was rewritten. It now looks at the user (authenticated, groups,
roles), the tenant, the agent, the tool (name, source ref, tags such as `external`), the
side-effect class, the data classification, the call's arguments and the agent's risk tier, and
returns one of three answers: `allow`, `deny` or `require_approval`, with reasons and the policy
version.

- **Hard denials** (any one wins): not authenticated; a kill switch for the tenant or agent;
  data owned by another tenant; a tool the agent's spec does not grant; data above the user's
  clearance (restricted needs `restricted-readers`); an unknown side effect; an argument above
  its limit for the risk tier (for example `amount_aud` over 10 000 in a medium-risk agent).
- **Approvals:** every irreversible action; external-facing writes in medium-risk agents; any
  write in a high-risk agent. High-risk irreversible actions need two-person approvers.
- The changeable parts (kill switches, clearances, limits) live in
  `policies/agent/config/data.json`, so operators change data, not code. `just provision-policies`
  now loads data files too.
- `ToolSpec` gained `tags`; `PolicyInput` gained `tool_ref`, `tool_tags` and `allowed_tools`.

## Why
Like a bank teller's rulebook: the same request can be fine, refused, or "needs a manager",
depending on who asks, for which account, how much, and how risky the branch is. Writing the rules
down in one place means every tool call is judged the same way.

## How to check it
`just policy-test` prints `PASS: 21/21` (allow, deny and approval-required cases, including
cross-tenant, kill switch, clearance and argument limits). With the stand-in up, `just
provision-policies` prints `opa: data agent/config: loaded`.

## Risks and follow-ups
The limits in `data.json` are examples for the invoice reference app, not business rules; set
real values per deployment. Production loads signed bundles instead of PUTs (Step 38).
