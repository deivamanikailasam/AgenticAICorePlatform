# ADR 0042: One hash-chained audit trail in Postgres; versioned, traced, redacted events; reconstruction from audit plus checkpoints

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 31
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 31 asks for new event types in the hash-chained audit log:
- run started and ended, plan;
- each tool call, with arguments hashed and sensitive values redacted;
- policy decision, guardrail result, approval request and decision, memory write, delegation, A2A
  call.

Each event records the user, the agent, the versions (spec, prompt, model, tool) and the trace id.

Done when:
- the audit verifier proves the chain intact;
- a run can be reconstructed from audit plus checkpoints;
- tampering with one event is detected.

Before this step each service wrote its own hash-chained JSONL file (`JsonlAuditSink`, Step 2).
Events had no versions or trace id. Tool calls recorded raw arguments, and the run itself was not
recorded at all. The spec says "the Project 0 hash-chained audit log". Project 0's audit service
schema and API can't be read from this build environment (open issue 1).

## Decision
1. **A `libs/agent-audit` library** with:
   - the event catalogue, the only vocabulary the trail uses;
   - redaction;
   - version and trace stamping;
   - the Postgres sink, the verifier and run reconstruction.
2. **One chain for every service.** It lives in `agent_audit.events` (migration 0008) and is used
   by agent-api, agent-worker, mcp-gateway and a2a-gateway.
   - The hash rule is the existing one: `sha256(canonical(prev, seq, event))`. Chains written
     before this step still verify, because empty new fields are left out of the hashed body.
   - Appends take a transaction-scoped advisory lock, so concurrent writers produce one unbroken
     chain.
   - Triggers refuse `UPDATE`, `DELETE` and `TRUNCATE`. The chain is what catches someone who
     bypasses the triggers.
3. **Stamping at the sink.** `StampingAuditSink` wraps whatever sink the service uses.
   - It adds the versions registered for the event's agent and the trace id: from the run
     context, else the current span, else the trace remembered for the run.
   - Call sites stay unchanged, and no event can forget its versions.
4. **Arguments: digest always, content redacted.**
   - `arguments_digest` is the same digest approval evidence binds to (Step 15), so "approved X"
     and "ran X" match exactly.
   - Recorded arguments have sensitive keys and secret shapes replaced, and long text cut.
   - Run input and output are recorded only as digests. The content stays encrypted in the run
     store and checkpoints.
5. **Selection by environment.**
   - `AGENT_AUDIT_DSN` set: Postgres.
   - Otherwise a JSONL file, in dev and CI only.
   - In staging and prod a service refuses to start without the shared trail.
   - The switch is its own variable, not `AGENT_DB_DSN`, so moving the run store doesn't silently
     move the audit trail.
6. **Verification and reconstruction as commands.**
   - `just audit-verify` names the first broken record and why.
   - `just audit-replay <run>` rebuilds the run's story from the trail plus its checkpoints and
     cross-checks the two: event hashes, start/end bracket, checkpoints within the audited plan,
     approved calls ran with the approved arguments, output digest.
7. **Project 0's audit log is VERIFY-ON-MAC.** The trail implements the `AuditSink` contract. If
   Project 0's audit service turns out to be the system of record, an adapter that forwards each
   record (with our hash, as a nested chain) is one class behind the same contract.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Keep per-service JSONL files | Four partial stories per run; files on container disks are lost on redeploy and editable by anyone with a shell |
| Write straight into Project 0's audit service | Its schema and API aren't readable from this environment; guessing would mean an untested integration. Wins once verified on the Mac |
| An append-only store with WORM retention (S3 Object Lock, immudb) | Stronger against an administrator than triggers plus a chain, at the cost of another service. Worth it when a regulator requires WORM; the sink contract allows it |
| A chain per tenant or per run | Removes the single append lock, but whole-trail ordering and "nothing was removed between runs" become harder to prove. The fallback if the lock becomes a bottleneck |
| Store full arguments, encrypted | More useful to investigators, but the audit trail then holds personal data subject to erasure, which a hash chain can't delete without breaking. Digest plus redacted copy avoids that |

## Consequences
- Audit writes are serialised by one lock. That is fine at the stand-in's volume, and not
  measured beyond it.
- Redaction is pattern-based. A secret in an innocently named field with no recognisable shape is
  kept, cut to 200 characters. The digest stays exact.
- A run whose output a budget summariser replaced fails the output cross-check by design.
- A staging or prod deployment without `AGENT_AUDIT_DSN` won't start.
- **VERIFY-ON-MAC:** whether Project 0's audit service must receive these events, and its schema.
