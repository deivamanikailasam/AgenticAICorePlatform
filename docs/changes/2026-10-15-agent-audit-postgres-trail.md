# agent-audit: event catalogue, redaction, stamping and the Postgres hash-chained trail

**Step:** 31 · **Commit:** feat(agent-audit): event catalogue and hash-chained Postgres trail · **Date:** 2026-10-15

## What changed
New library `libs/agent-audit`:
- **`events.py`.** The catalogue of every audit event type, with what each one's payload must carry. `validate(event)` rejects unknown types and missing fields.
- **`redact.py`.** `audited_arguments(arguments)` returns the arguments' SHA-256 digest (the same digest approvals bind to) and a redacted copy:
  - sensitive keys (password, token, secret, api_key, card…) become `[redacted]`
  - secrets and PII patterns found by the guardrails are replaced with placeholders
  - long text is truncated
- **`stamping.py`.** `StampingAuditSink` wraps any sink. It stamps each event with:
  - the versions registered for its agent
  - its trace id, taken from the run context, else the current span, else the trace remembered for the run. That last case covers approval events, which happen outside the run's span.
- **`postgres.py`.** `PostgresAuditSink` (`audit_sink.postgres`): one chain in `agent_audit.events` shared by every service.
  - Appends are serialised by a transaction-scoped advisory lock.
  - `check()` recomputes every hash and names the first broken row, with a reason: missing, does not link, or hash does not match.
- **`verify.py`.** The same check for any sequence of records, such as a JSONL trail.
- **`factory.py`.** `audit_sink_from_env`: Postgres when `AGENT_AUDIT_DSN` is set, else the service's JSONL file in dev or CI. In staging and prod the service refuses to start.
- **`migrations/0008_audit.sql`.** The table plus triggers that refuse `UPDATE`, `DELETE` and `TRUNCATE`.

## Why
Step 31 needs one trail that every service writes, that can't be quietly edited, and whose events are self-describing. The triggers stop ordinary changes. An administrator can bypass triggers, and the hash chain is what catches that.

## How to check it
- `uv run pytest libs/agent-audit`: catalogue, redaction, stamping, factory.
- With the stand-in (`-m integration`):
  - the AuditSink contract
  - 4 writers × 10 events concurrently giving one intact 40-record chain
  - update, delete and truncate refused
  - three administrator tampers (edit a payload, delete a row, relink a row), each detected at the right row with the right reason

## Risks and follow-ups
- Every append takes the same lock, so audit writes are serialised. That is fine at the stand-in's volume. A partitioned chain (one per tenant) is the fallback if it becomes a bottleneck.
- **VERIFY-ON-MAC:** whether Project 0's audit-log service should receive these events as well. Its schema isn't available from this environment.
