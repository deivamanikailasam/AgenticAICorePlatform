# The run API keeps its runs through a restart, and can rewind a thread

**Step:** 18 · **Commit:** feat(agent-api): keep runs through restarts and fork threads from a checkpoint · **Date:** 2026-10-15

## What changed
- Runs and their full AG-UI event logs are saved in a run store: in memory (default, one
  process) or in Postgres (`AGENT_API_STORE=postgres`), where run details and every event are
  encrypted with the checkpoint key and only lookup columns are in clear. Access tokens are
  never stored.
- With a checkpointer, every node of every run is checkpointed (encrypted). When the service
  starts it reloads every stored run: runs that were working continue from their last checkpoint
  (finished steps are not repeated, events are not sent twice), and runs waiting for an approval
  are brought back to that point so they can still be approved.
- Time travel: `GET /v1/threads/{id}/checkpoints` lists a thread's checkpoints;
  `POST /v1/threads/{id}/fork` starts a new thread and run from a chosen checkpoint, without
  repeating the steps before it. Only the thread's owner can do either.
- New migration `0003_runs.sql` (runs and run events).

## Why
Step 18: runs must survive the service restarting or a worker dying, and people need to be able
to look back at a run and retry from a point in it.

## How to check it
`uv run pytest services/agent-api` — 15 passed, including: a paused run survives a restart and
is approved afterwards with nothing repeated; a run that was mid-step when the process died
continues without re-running its finished tool step; a fork continues after the chosen
checkpoint. With the stand-in stack: `uv run pytest -m integration
services/agent-api/tests/test_store_integration.py` (round trip, nothing readable at rest).

## Risks and follow-ups
After a restart a resumed run has no user token (tokens are never stored), so tools that act on
behalf of the user (rag-core search) fail until delegated credentials arrive (Step 29). The
compose service still uses the memory store by default; production sets
`AGENT_API_STORE=postgres` with the key from OpenBao.
