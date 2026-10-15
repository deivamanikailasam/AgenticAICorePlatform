# ADR 0029: Checkpoints at the node-runner level, encrypted in Postgres; resume by journal replay

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 18
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 18 asks for a Postgres checkpointer in the `agent` database (a snapshot after every node,
keyed by tenant, thread and run), time travel (inspect and fork from an earlier checkpoint),
retention by policy and encryption of sensitive fields, with Valkey (dev) or SQLite (tests) as
alternatives. Done when: kill the worker mid-run; the run resumes from the last checkpoint
without repeating a completed write tool.

Checked 2026-10-15: `cryptography` 50.0.2 (2026-09-30) and `psycopg` 3.3.x are already locked;
no new package. `langgraph-checkpoint-postgres` exists, but it covers LangGraph only.

## Decision
1. **Checkpoint where node semantics live**, in the platform's node runner
   (`CheckpointingNodeRunner`), not in each engine's own checkpoint format. The same checkpoints
   then work for LangGraph and Agent Framework, and record node outcomes and approval decisions
   in the platform's own terms.
2. **Resume by journal replay.** `load_journal` rebuilds a run's history from its checkpoints; a
   runner with `resume=True` answers finished nodes from it and runs the rest live; adapters ask
   `runner.replays(node)` to decide whether an approval is still needed. A tool node that was in
   flight is protected by its stable idempotency key (`<run>:<node>`, Step 6/12).
3. **Postgres default** (`agent_runtime.checkpoints`), **Valkey** for development, **SQLite** and
   memory for tests; all pass the checkpointer contract.
4. **Field encryption** with AES-256-GCM for `input`, `values`, `value`, `error`, `transcript`;
   tenant, thread and checkpoint id are associated data (no row swapping). The key is a secret
   reference (OpenBao in production).
5. **Retention** by age with a floor of the newest N per thread; **time travel** as `history` and
   `fork_thread` over the checkpointer interface (works on every backend).
6. **The run API is durable**: runs and AG-UI event logs in a run store (Postgres, encrypted);
   on start it reloads runs, resumes running ones and restores paused ones silently; fork and
   inspect endpoints for the owner. Access tokens are never stored.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Each engine's checkpointer (LangGraph's Postgres saver, Agent Framework's storage) | Two formats, two migrations, no shared replay or approval semantics; useful later for engine-internal features (Step 19+) |
| Encrypt the whole row | Retention, lookup and debugging need tenant, thread, run, node and step in clear |
| Database-level encryption only (TDE) | Protects disks, not a leaked dump or an over-broad database grant |
| Replay by re-executing with idempotency only | Model calls and functions would run again (cost, different answers) |

## Consequences
A run resumed after a restart has no user token; on-behalf-of tools fail until delegated
credentials (Step 29). Key rotation needs re-encryption (Step 29). Multi-day waits belong on
Temporal (Step 19).

## Verification
`libs/agent-runtime/tests/test_checkpoints.py`, `test_checkpoints_integration.py` (10);
`libs/agent-compiler/tests/test_durable_runs.py` (SIGKILL mid-run, both adapters; Postgres
variant); `services/agent-api/tests/test_api.py` (restart and fork), `test_store_integration.py`.
