# Services write the shared audit trail

**Step:** 31 · **Commit:** feat(services): write the shared hash-chained audit trail · **Date:** 2026-10-15

## What changed
- agent-api, agent-worker, mcp-gateway and a2a-gateway build their audit sink with `audit_sink_from_env`:
  - the shared Postgres trail when `AGENT_AUDIT_DSN` is set
  - else their local JSONL file (dev and CI only)
- Compose passes `AGENT_AUDIT_DSN` from `AGENT_AUDIT_DSN_INTERNAL`.
- CI sets `AGENT_AUDIT_DSN_INTERNAL`, so the live services in the integration job write to `agent_audit.events`.

## Why
Four separate files can't show one run's full story across the API, the worker and the gateways, and anyone with a shell can edit a file. The switch is its own variable rather than `AGENT_DB_DSN`, so a service that uses the agent database only for runs or memory doesn't silently move its audit trail.

## How to check it
Run the agent-api subprocess integration test with `AGENT_AUDIT_DSN` set, then `just audit-verify`. Run locally against the stand-in, the service wrote run.started, plan, policy.decision, approval.requested, approval.decided and run.ended events, and the verifier reported "OK, 9 records, chain intact". In that local run, the approval test itself failed at the policy call, because OPA was not running in the partial stand-in. CI runs the full stack.

## Risks and follow-ups
A staging or prod service without `AGENT_AUDIT_DSN` now refuses to start. That is deliberate.
