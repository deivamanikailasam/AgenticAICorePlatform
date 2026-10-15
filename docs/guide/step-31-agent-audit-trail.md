# Step 31 · Agent audit trail

## 1. At a glance

| | |
|---|---|
| **Goal** | A permanent, tamper-evident record of everything agents did and why, from which any run can be rebuilt |
| **Time** | About a day and a half |
| **You end up with** | `libs/agent-audit`: event catalogue, redaction, version and trace stamping, the Postgres hash-chained trail (migration 0008), the verifier and run reconstruction; every service writing one shared trail; `just audit-verify` and `just audit-replay` |
| **New words** | audit trail, hash chain, append-only, digest, redaction, reconstruction |

## 2. The big idea

Traces (Step 30) answer "what happened and how fast?", and they expire after days. An audit trail
answers "who did what, under which rules, and who allowed it?". It is kept for years, and must be
believable even to someone who doesn't trust the operators.

Two properties make it believable:
- **Every event links to the one before it with a hash**, a fingerprint that changes completely if
  a single character changes. Edit, remove or reorder one event, and every fingerprint after it
  stops matching.
- **Each event describes itself**: the user, the agent, the exact versions running, and the trace
  id. Nobody has to cross-reference other systems to know what an event means.

> *Analogy:* a bank's passbook where every line also prints a code computed from the line above.
> Tearing out a page, or changing an amount, leaves codes that no longer add up, and anyone with a
> calculator can find exactly where.

## 3. What we're doing

1. Define the catalogue of event types, with what each must contain.
2. Stamp every event with versions and the trace id, and record tool arguments as a digest plus a
   redacted copy.
3. Record runs (start, plan, end), not just tool calls.
4. Store one chain for all services in Postgres, append-only, safe with concurrent writers.
5. Verify the chain, detect tampering, and rebuild any run from audit plus checkpoints.

## 4. Why we're doing this

When an agent pays the wrong invoice, the questions are:
- who asked;
- which agent and prompt version;
- which model;
- what the tool was told to do;
- did policy allow it, and who approved it.

Without a trail those answers come from logs that are partial, expire, and can be edited.
Regulators and security reviews ask the same questions. OWASP's agentic risks (repudiation,
rogue agents) assume one exists.

> *Watch out:* an audit trail is also a data store. Logging raw arguments puts passwords and
> personal data in a place designed never to delete anything.

## 5. Key ideas

- **Catalogue.** `agent_audit.events.CATALOGUE` lists every type: `run.started`, `plan`,
  `tool.call`, `policy.decision`, `approval.decided`, `agent.delegate`, `a2a.call` and the rest.
  `validate()` rejects unknown types and missing fields, so the trail stays a known vocabulary.
- **Versions.** Each event carries:
  - the spec version and its hash;
  - the plan hash and prompt refs;
  - the model alias per role;
  - each tool's version.
- **Trace id.** The same id Tempo and Langfuse show, so the trail joins to traces.
- **Digest, not content.**
  - Tool arguments are recorded as a SHA-256 digest (identical to the one an approval binds to)
    plus a copy with passwords, tokens, card numbers and secret-looking strings replaced.
  - Run input and output are recorded as digests only. The content stays encrypted in the run
    store and checkpoints.
- **Append-only plus a chain.**
  - Database triggers refuse updates, deletes and truncation.
  - An administrator can switch triggers off. The chain is what catches that.
- **Reconstruction.** The trail says who, what, which versions and who allowed it. Checkpoints say
  what each step produced. Together they rebuild the run, and the tool checks that the two agree.

## 6. How to do it

```bash
just migrate                          # creates agent_audit.events (migration 0008)
AGENT_AUDIT_DSN=postgresql://…        # every service then writes the shared trail
just audit-verify                     # OK, N records, chain intact | BROKEN at record N: <why>
just audit-verify --file /tmp/agent-api-audit.jsonl    # a dev service's local trail
just audit-replay <run_id>            # the run's story plus five cross-checks
```

`just audit-replay` prints the run's header (tenant, user, agent and versions, trace ids,
status), then every audit event and checkpoint in time order, then the checks. This is an
abbreviated real output from the test run:

```
agent pay-total 0.1.0  plan 52f3f9388391  …  tools {'calculator': '1.0.0', 'pay': '1.0.0'}
…  audit      approval.decided   c2d5… approve by j.smith tools=['pay']
…  checkpoint approval           pay.approval ok
…  audit      tool.call          pay ok=True args={'amount_aud': 40.0}
PASS  approved calls ran with the approved arguments: 1 calls
PASS  output matches run.ended
```

## 7. Benefits

- **One place for the whole story.** One trail covers the API, worker and gateways, ordered and
  verifiable.
- **Tampering is detectable,** and the verifier says exactly where.
- **Approvals are provable.** "Approved X" and "ran X" are linked by the same digest.
- **No secrets or personal data** are stored in a place that can't delete.

## 8. Drawbacks and risks

- **One lock.** Appends are serialised by a single lock. That is fine at stand-in volume; per-tenant
  chains are the fallback.
- **Redaction is pattern-based.** An oddly named field holding a secret with no recognisable shape
  is kept, truncated.
- **Triggers don't stop administrators.** The chain detects their changes but doesn't prevent
  them. WORM storage would.
- **Project 0's own audit service** may need these events too. That is VERIFY-ON-MAC.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Postgres trail with triggers and a chain | S3 Object Lock or immudb (WORM) | A regulator requires write-once storage |
| One chain | A chain per tenant | Audit volume makes the single lock a bottleneck |
| Digest plus redacted arguments | Full arguments, encrypted | Investigators need full content and an erasure process exists outside the chain |
| Own trail behind `AuditSink` | Project 0's audit service directly | Once its schema and API are verified on the Mac |

## 10. Which approach fits which situation

- **"Was anything changed?":** `just audit-verify`, on a schedule and before any audit export.
- **"What did this run do?":** `just audit-replay <run>`, then the trace id in Tempo and Langfuse
  for timing and (redacted) content.
- **"Who approved this payment?":** the `approval.decided` event's `by` and `calls` digest,
  matched to the `tool.call`.
- **Local development:** no `AGENT_AUDIT_DSN`, so each service keeps a JSONL trail that
  `just audit-verify --file` checks.

## 11. Common mistakes

- Logging full tool arguments "for debugging". The trail keeps them forever.
- Hashing `model_dump()` instead of `chained()`. Old chains stop verifying.
- Adding an event type without adding it to the catalogue. `validate()` and the tests refuse it.
- Pointing `AGENT_AUDIT_DSN` at a database without migration 0008. Run `just migrate` first.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| The audit verifier proves the chain intact | ✅ (stand-in) | `test_concurrent_writers_keep_one_unbroken_chain`: 4 writers × 10 events give one chain of 40 that `check()` passes. `test_the_shared_postgres_trail_verifies`: `just audit-verify` passes on the shared trail. Live: agent-api writing through `AGENT_AUDIT_DSN` produced 9 events, and `just audit-verify` reported "OK, 9 records, chain intact" |
| A run can be reconstructed from audit plus checkpoints | ✅ (stand-in) | `test_a_run_is_reconstructed_from_audit_plus_checkpoints`: an approved payment is rebuilt with all five checks passing, its output rebuilt from checkpoints. `test_just_audit_replay_reconstructs_a_run_from_postgres` does the same from the Postgres trail and encrypted Postgres checkpoints. `test_reconstruction_catches_records_that_disagree`: a forged checkpoint and a changed call digest are each caught |
| Tampering with one event is detected | ✅ (stand-in) | `test_tampering_with_one_event_is_detected` (as an administrator bypassing the triggers): edited payload, "hash does not match the event" at row 2; deleted row, "row 2 is missing"; relinked row, "does not link" at row 3. `test_the_trail_is_append_only`: update, delete and truncate refused. JSONL: `test_an_edited_record_is_named`, `test_a_removed_record_is_named` |
| Event types, user, agent, versions, trace id, redacted arguments | ✅ | `test_the_catalogue_covers_the_spec_event_types`, `test_arguments_are_hashed_and_recorded_redacted`, `test_every_event_carries_versions_and_the_trace_id`; `test_an_approved_payment_leaves_a_complete_versioned_trail` (every event validates and names user, agent, versions and an exported trace); team run: delegations and worker calls with the worker's own versions |
| ADR | ✅ | ADR 0042 |

## 13. Recap

Every service now writes one shared trail. Each event is from a known catalogue, says who, which
agent, which versions and which trace, and records tool arguments as a digest plus a redacted copy.
The chain makes any edit visible, and the verifier says where. The run can be rebuilt from the
trail plus its checkpoints, with checks that the two agree. The remaining question, whether Project
0's own audit service must also receive these events, is left for the Mac.
