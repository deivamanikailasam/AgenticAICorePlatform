# A run survives its worker dying, and never pays twice

**Step:** 18 · **Commit:** feat(agent-runtime): checkpoint every node and resume runs after a crash · **Date:** 2026-10-15

## What changed
- `CheckpointingNodeRunner` saves a checkpoint after every node and every approval decision:
  tenant, thread, run, step, node, outcome and value (sensitive fields encrypted by the Postgres
  checkpointer).
- `load_journal` rebuilds a run's history from its checkpoints. A runner with `resume=True`
  answers every node that already finished from that history (no model or tool call) and runs
  the rest live, including an approval that had not been decided yet. Both runtime adapters use
  this to decide whether to ask for an approval again.
- Live progress events are not repeated for nodes answered from the history.

## Why
Step 18 "Done when": kill the worker mid-run; the run resumes from the last checkpoint without
repeating a completed write tool.

## How to check it
`uv run pytest libs/agent-compiler/tests/test_durable_runs.py` — on both runtimes, a worker
process writes a record, starts a slow step and is killed with SIGKILL; restarted with the same
run id, it finishes and the record was written exactly once. With the stand-in stack, the
`-m integration` test does the same with encrypted Postgres checkpoints.

## Risks and follow-ups
A node that was in the middle of running when the worker died runs again; for tools, the
platform's idempotency key (`<run>:<node>`) with the shared Valkey store stops a second write,
but functions and model calls simply repeat. Long runs that wait days belong on Temporal
(Step 19).
