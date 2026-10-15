# Durable approval gate in Postgres; OPA routing in deployments

**Step:** 28 · **Commit:** feat(agent-runtime): durable approval gate in Postgres · **Date:** 2026-10-15

## What changed
- `approval_gate.postgres` (`agent_runtime.approval_store.PostgresApprovalGate`) stores
  approval requests and decisions in `agent_runtime.approvals` (migration 0007).
  - Decisions take a row lock, so two reviewers can't both win.
  - A request past its expiry reads as `expired` and can't be decided.
- The run API uses the Postgres gate when `AGENT_DB_DSN` is set, and the OPA approval router
  when the policy engine is OPA. With Cerbos, or without a database, it uses the in-process
  rules router and the in-memory gate.

## Why
Step 28's approvals must outlive a process restart, and the routing must come from the
platform's policy engine.

## How to check it
With the stand-in, after `just migrate` applied 0007:
`uv run pytest libs/agent-runtime/tests/test_approval_store_integration.py -m integration`
passes 6 tests: the shared `ApprovalGateContract` (edit, decide once, expiry, unknown id) plus
survival across gate instances.

## Risks and follow-ups
- The SLA sweeper runs in each API process. With several replicas, two can act on the same
  overdue approval; the row lock lets only one decision win. A leader-elected sweeper is a
  Step 37 production item.
