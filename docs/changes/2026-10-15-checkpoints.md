# Run snapshots that survive a restart, encrypted, prunable and forkable

**Step:** 18 · **Commit:** feat(agent-runtime): add the Postgres checkpointer with encryption, retention and time travel · **Date:** 2026-10-15

## What changed
- `checkpointer.postgres`: snapshots in the `agent` database (`agent_runtime.checkpoints`, new
  migration), keyed by tenant, thread and run, oldest to newest.
- **Encryption of sensitive fields:** the run's input, node values, errors and transcripts are
  encrypted with AES-256-GCM before they reach the database. The key comes from a secret
  reference (OpenBao in production). Each ciphertext is tied to its tenant, thread and
  checkpoint, so copying it to another row or tenant makes it unreadable.
- **Retention:** `prune(older_than, keep_last)` deletes old snapshots but always keeps each
  thread's newest ones, so a thread can still be resumed.
- **Time travel:** `history` lists a thread's snapshots (without their contents); `fork_thread`
  copies a thread up to a chosen snapshot into a new thread to continue differently.
- `checkpointer.valkey`: the same in Valkey, for development only. `checkpointer.sqlite`
  (tests) and `checkpointer.memory` already existed.

## Why
Step 18: a run must be able to continue after its worker dies, and its saved state holds
customer data, so it must be encrypted and not kept forever.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_checkpoints.py` (encryption and binding, history,
fork). With the stand-in stack (`just migrate` first):
`uv run pytest -m integration libs/agent-runtime/tests/test_checkpoints_integration.py` — 10
passed: Postgres and Valkey pass the checkpointer contract, the stored row holds no plaintext of
sensitive fields, and pruning keeps the newest snapshot.

## Risks and follow-ups
Losing the key makes old snapshots unreadable; key rotation (re-encrypting with a new key) comes
with OpenBao in Step 29. Valkey offers no retention or durability guarantees and is for
development only.
