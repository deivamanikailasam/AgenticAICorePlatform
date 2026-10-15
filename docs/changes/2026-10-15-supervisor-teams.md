# Supervisor teams: one agent delegates to workers, each with its own tools and budget

**Step:** 22 · **Commit:** feat(agent-runtime): supervisor teams with typed shared state · **Date:** 2026-10-15

## What changed
- **`pattern: supervisor` and `pattern: hierarchical`** build one SUPERVISOR node over the
  spec's `agents`. A new `supervisor:` section sets the dispatcher (`llm` or `rules`),
  `max_rounds`, `max_depth`, `max_failures`, the typed shared `state` (field to JSON Schema)
  and the `output` field. Each worker lists the fields it `reads` and `writes`, and the spec
  checks reject fields that are not in the state.
- **`SupervisorExecutor`** (in `agent_runtime.supervisor`) runs rounds: the dispatcher picks a
  worker and a task, or finishes. The choice is then checked:
  - the worker must belong to the team;
  - policy is asked (`agent.delegate`);
  - the worker's circuit must be closed;
  - the fields it reads must already be in the state.
- **Workers run as child runs** with their own spec, tools, grants, model roles and budget,
  built by a worker factory. They get only the task and the fields they read. Only the fields
  they write are kept, each validated against the schema. Worker output is untrusted, so the
  LLM dispatcher sees it delimited.
- **Bounds:** the team's `max_cost_aud` covers all workers; a worker that fails `max_failures`
  times stops being chosen; nesting stops at `max_depth`.
- **Per-agent cost in the trace.** Each worker run is an `invoke_agent <worker>` span in the
  team's trace, with AUD cost and tokens from a meter. The result lists cost per agent.
- **Smaller changes:**
  - `delegation_authoriser` asks the platform PDP about `agent.delegate` and audits each
    decision.
  - The in-process rules PDP now refuses tools outside an agent's grants, as OPA does.
  - The metering wrappers moved to `agent_compiler.metering`.
- **Reference app `agents/research-report`:** a researcher (rag-core search), an analyst
  (calculator) and a writer (word count) produce a short cited report.

## Why
Step 22: teams of agents where each agent keeps its own tools, permissions and budget, so a
confused or compromised worker cannot use another's powers (OWASP ASI07, ASI08).

## How to check it
`uv run pytest libs/agent-compiler/tests/test_supervisor.py` (11 tests):
- the reference app runs with three workers, with AUD 0.002 cost per worker on its span;
- workers see only the fields they read;
- the writer cannot call the researcher's search tool, at the loop and again at policy;
- unknown workers and policy-denied delegations are refused;
- extra fields in a worker's output are dropped, and it is shown to the supervisor delimited;
- the circuit breaker, the budget cap and the nesting limit each hold;
- the rules dispatcher runs the team with no model.

## Risks and follow-ups
- Worker runs are not checkpointed separately. A crash re-runs the current worker (tools stay
  protected by idempotency keys).
- The dispatcher sees state values truncated to 4,000 characters each.
