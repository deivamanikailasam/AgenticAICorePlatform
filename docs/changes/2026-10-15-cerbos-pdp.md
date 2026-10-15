# A second policy engine that makes the same decisions: Cerbos

**Step:** 10 · **Commit:** feat(agent-tools): add the Cerbos policy decision point · **Date:** 2026-10-15

## What changed
- `policy_decision_point.cerbos`: a client for Cerbos's HTTP API, and `policies/cerbos/tool.yaml`,
  the same rules as the OPA policy written in Cerbos's YAML. Cerbos only says allow or deny per
  action, so each call is asked as two actions: `call` (deny means deny) and
  `call_without_approval` (deny means "needs approval"). Reasons and approvers come back as rule
  outputs. Like the OPA client it fails closed.
- Cerbos runs in Project A's compose under the `cerbos` profile (`just up-alternatives`); Project
  0 does not run it. The gateway picks the engine with `MCP_GATEWAY_PDP=opa|cerbos` (default opa).
- `just policy-test` also compiles the Cerbos policy and runs its 9 tests.
- A live parity test runs 19 cases through both engines and requires the same effect and
  approvers; a unit test fails if the Cerbos constants drift from `policies/agent/config/data.json`.

## Why
Like having two referees who learned the same rulebook: if your organisation already uses
Cerbos, you can switch to it, and the parity test proves it would blow the whistle at exactly the
same moments as the default.

## How to check it
`just policy-test` ends with `9 tests executed [9 OK]`. With the stand-in and
`just up-alternatives` running, `uv run pytest -m integration libs/agent-tools/tests/test_policy_cerbos.py`
passes the contract and all 19 parity cases.

## Risks and follow-ups
Two copies of the rules can drift; the parity and constants tests are the guard, so keep adding
cases to both when a rule changes. Cerbos's reason wording differs slightly from OPA's in places;
only effects and approvers are compared.
