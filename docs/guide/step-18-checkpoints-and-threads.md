# Step 18 · Checkpoints and threads

## 1. At a glance

| | |
|---|---|
| **Goal** | Runs that survive crashes and restarts, never repeat a finished action, keep customer data encrypted, and can be rewound |
| **Time** | About two days |
| **You end up with** | `checkpointer.postgres` (plus Valkey, SQLite, memory), field encryption, retention, time travel (inspect, fork), resume after a crash, a durable run API |
| **New words** | checkpoint, thread, journal, replay, resume, retention, fork |

## 2. The big idea

After every step the platform writes down what happened: which step ran, how it ended, what it
produced. If the machine running the agent dies, a new one reads those notes, skips everything
already done and carries on from the next step. Anything already paid stays paid once.

> *Analogy:* a recipe card where you tick each step as you finish it. If you are called away,
> whoever takes over sees the ticks and starts at the first unticked step, instead of adding the
> salt twice.

## 3. What we're doing

1. Store a checkpoint after every node and every approval decision, in Postgres.
2. Encrypt the sensitive parts of each checkpoint.
3. Rebuild a run's history from checkpoints and resume from it.
4. Delete old checkpoints by policy without breaking resumable threads.
5. Let people inspect a thread and fork it from an earlier point.
6. Make the run API keep its runs through restarts.

## 4. Why we're doing this

Agents run for minutes to days and wait for people. Machines restart, deploys happen, workers
crash. Without checkpoints a crash loses the run or, worse, re-runs steps that already paid,
emailed or deleted something.

> *Watch out:* "resume" only helps if finished steps are truly skipped. That is exactly what the
> Done-when test checks: the write happens once, even though the worker was killed mid-run.

## 5. Key ideas

- **Checkpoint.** Tenant, thread, run, step number, node, outcome and value, after each node.
- **Thread.** A conversation or case: one or more runs share it, and its checkpoints form a line.
- **Journal and replay.** The run's list of finished steps. On resume, finished steps are
  answered from the journal (no model call, no tool call); the rest run live.
- **Encryption.** Inputs, values, errors and transcripts are encrypted with a key from the secret
  store; each ciphertext is tied to its tenant, thread and checkpoint.
- **Retention.** Old checkpoints are deleted after a set time, but the newest few of each thread
  are kept so it can still resume.
- **Fork.** Copy a thread up to a chosen checkpoint into a new thread and continue differently
  from there.
- **Durable run API.** Runs and their event logs are stored (encrypted); after a restart, working
  runs continue and paused runs still wait for their approval.

## 6. How to do it

Run the database migrations, then start the API with durable storage:

```bash
just migrate
# dev only: production takes the key from OpenBao
AGENT_API_STORE=postgres \
AGENT_DB_DSN_INTERNAL=postgresql://agent_app:...@postgres:5432/agent \
AGENT_CHECKPOINT_KEY_DEV="$(openssl rand -base64 32)" just up
```

Inspect a thread and fork it:

```bash
curl -s http://127.0.0.1:8091/v1/threads/<thread>/checkpoints -H "Authorization: Bearer $TOKEN"
curl -s -X POST http://127.0.0.1:8091/v1/threads/<thread>/fork -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"checkpoint_id": "<id>"}'
```

Prune old checkpoints (for example from a nightly job):

```python
await PostgresCheckpointer(dsn, cipher).prune(older_than=timedelta(days=30), keep_last=5)
```

## 7. Benefits

- Crashes and deploys no longer lose runs or repeat side effects.
- People can see every step of a run afterwards and branch from any point.
- Customer data in saved state is encrypted, and old state is cleaned up.
- The same mechanism works on both runtime engines.

## 8. Drawbacks and risks

- One database write per step adds a little latency.
- A step that was running at the moment of the crash runs again; tools are protected by their
  idempotency key, but model calls and functions repeat.
- Losing the encryption key makes old checkpoints unreadable; key rotation comes with OpenBao
  (Step 29).
- A run resumed after a restart has no user token, so tools that act on behalf of the user fail
  until delegated credentials (Step 29).

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Postgres checkpoints | Valkey | Local development where losing state is acceptable |
| Postgres checkpoints | SQLite / memory | Unit tests |
| Platform checkpoints | An engine's own checkpointer | Features only that engine offers (not needed so far) |
| Checkpoint per node | Durable workflows on Temporal (Step 19) | Runs that wait days, need timers and signals at scale |

## 10. Which approach fits which situation

- **Anything with write or irreversible tools:** Postgres checkpoints, always.
- **Quick experiments on a laptop:** memory or Valkey.
- **Customer support case that needs a "what if":** fork the thread at the checkpoint before the
  decision and run it again.
- **Data that must be deleted after N days:** set retention to N days; keep the newest few per
  active thread.

## 11. Common mistakes

- Running production with the memory store: a restart loses every run.
- Storing the key next to the database: it belongs in the secret store.
- Pruning with `keep_last=0` on threads that are still waiting for an approval.
- Expecting a resumed run to call rag-core as the user: it has no token until Step 29.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| Kill the worker mid-run; it resumes from the last checkpoint without repeating a completed write tool | ✅ | a worker process writes a record, starts a slow step and is killed with SIGKILL; restarted with the same run id it finishes and the record exists exactly once — on LangGraph and Agent Framework (SQLite checkpoints) and with encrypted Postgres checkpoints |
| Postgres checkpointer: a snapshot after every node, keyed by tenant, thread and run | ✅ | passes the checkpointer contract (latest, history, tenant isolation, delete) against the stand-in Postgres |
| Encryption of sensitive fields | ✅ | the stored row contains no plaintext of a sensitive value; a ciphertext moved to another tenant does not decrypt |
| Retention by policy | ✅ | pruning removes old checkpoints and keeps each thread's newest |
| Time travel: inspect and fork | ✅ | history lists steps without their data; a fork copies up to the chosen checkpoint into a new thread, and a forked run continues after it without repeating earlier steps |
| Valkey (dev) and SQLite (tests) alternatives | ✅ | Valkey passes the contract against the stand-in; SQLite drives the crash test |
| Durable run API | ✅ | a paused run survives a restart and is approved afterwards; a run that was mid-step continues without repeating its finished tool step; runs and events are encrypted at rest |
| ADR | ✅ | ADR 0029 |

## 13. Recap

Step 18 made runs durable. Every step is checkpointed, encrypted where it matters, and kept only
as long as policy says. A crashed or restarted worker rebuilds the run's history and continues
from the next step, so finished actions, payments included, are never repeated. Threads can be
inspected and forked from any earlier point, and the run API now keeps its runs and event logs
through restarts.
