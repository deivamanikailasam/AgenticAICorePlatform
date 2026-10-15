# Temporal runtime: agent runs as durable workflows

**Step:** 19 · **Commit:** feat(agent-runtime): run agents as Temporal workflows · **Date:** 2026-10-15

## What changed
New package `agent_runtime.temporal` (temporalio 1.34.0):
- `TemporalRuntime` (`runtime_adapter.temporal`). The `agent-run` workflow interprets the compiled plan.
- Every model, tool, function and agent node runs as the `run_node` activity, with a retry
  policy (5 attempts, backoff from 1 s to 30 s), a 10-minute limit and a 30-second heartbeat.
- Approval nodes wait for an `approve` signal behind a durable timer (7 days by default, then
  the outcome is `expired`). Joins are released when the other branches settle, and failed runs
  are compensated (saga).
- Values over 256 KiB are written to the artefact store and passed as `{"$ref": uri}`.
- `TemporalDurableExecutor` (`durable_executor.temporal`) runs registered functions as
  workflows that can wait for signals.
- Restate and DBOS are documented stubs.
- `durability: temporal` in a spec now selects the Temporal runtime, unless a
  `runtime_adapter` component is pinned.

## Why
Step 19: runs that wait days for people and survive worker and server restarts, without
re-running finished side effects.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_temporal.py libs/agent-compiler/tests/test_compiler.py -k "temporal or stub or reference"`

## Risks and follow-ups
- Workflow code must stay deterministic. The plan is interpreted in the workflow, so changing a
  plan while runs are in flight needs a new plan version (handle `name@version`).
- The server deployment and the restart tests are in the next commit.
