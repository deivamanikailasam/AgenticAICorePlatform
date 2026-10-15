# The default engine: LangGraph runs the compiled plans

**Step:** 11 · **Commit:** feat(agent-runtime): add the LangGraph runtime adapter · **Date:** 2026-10-15

## What changed
- `runtime_adapter.langgraph` turns a compiled plan into a LangGraph `StateGraph` (LangGraph
  1.2.12, pinned exactly). The run's state is typed and merged with reducers, so parallel
  branches never overwrite each other; edges pick their targets by outcome and several targets
  run in parallel; join nodes wait until every branch has finished; approval nodes pause with
  `interrupt()` and continue on `resume`; `budgets.max_steps` is LangGraph's recursion limit
  (hitting it gives status `stopped`); events stream as `node_started` / `node_finished` /
  `approval_requested`; each run executes in its own task, so `cancel` stops it mid-node.
- `agent_runtime.nodes` holds what a node *does* and where the run goes next, shared by every
  adapter so behaviour cannot drift between engines.
- `agents/hello-workflow` now runs on LangGraph (the platform default) instead of the Step 4
  stub; `just run` and the Tempo trace test pass on it.

## Why
Like hiring an experienced site manager instead of training a new one: LangGraph already knows
how to run steps in parallel, pause for sign-off and pick up where it left off. The platform
keeps the rules (budgets, approvals, events) and lets LangGraph do the scheduling.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_langgraph_adapter.py` (14 contract tests: the
basic runtime contract plus the workflow contract). `just run agents/hello-workflow/agent.yaml
'{"name":"Ada"}'` prints `hello, Ada` and a trace id.

## Risks and follow-ups
Checkpoints are in memory for now; Step 18 adds Postgres. LangGraph was pinned to a release at
least 14 days old (ADR 0022); upgrades go through the same cooldown.
