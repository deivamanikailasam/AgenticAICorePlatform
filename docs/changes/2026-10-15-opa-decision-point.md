# Ask OPA before every tool call, and fail closed

**Step:** 08 · **Commit:** feat(policy): add the OPA decision point and a baseline tool policy · **Date:** 2026-10-15

## What changed
- `policy_decision_point.opa` asks Project 0's OPA for a decision on each tool call. If OPA is
  down, errors, or returns anything that isn't a valid decision, the answer is **deny**.
- `policies/agent/tools/decision.rego` is the baseline policy: unauthenticated callers are denied;
  calls touching another tenant's data are denied; authenticated `read` calls are allowed;
  `write` and `irreversible` calls need a human approval. Step 10 extends it.
- `just provision-policies` loads the policies into OPA (dev/CI); `just policy-test` now also
  checks formatting and runs `opa check --strict`.
- `PolicyInput` gains `subject` (authenticated, groups, roles) taken from the caller's token.

A live test caught a real bug before commit: Rego treats JSON `null` as a value, so "tenant
unknown" was being treated as cross-tenant. The rule now checks `!= null`, with a test.

## Why
Policy as code means one tested, reviewable place that says yes or no, instead of `if`
statements scattered through tool code (ADR 0009). Failing closed means an outage can block
work but never wave it through.

## How to check it
`just policy-test` → `PASS: 7/7`. With the stand-in: `just provision-policies` then
`uv run pytest -m integration libs/agent-tools/tests/test_policy.py`.

## Risks and follow-ups
Project 0's OPA may load policies only from bundles; pushing over REST is for dev and CI
(VERIFY-ON-MAC). Signed bundles arrive in Step 38.
