# A second engine proves the seam: Microsoft Agent Framework

**Step:** 11 · **Commit:** feat(agent-runtime): add the Agent Framework adapter and the CrewAI stub · **Date:** 2026-10-15

## What changed
- `runtime_adapter.agent_framework` runs the same compiled plans as Microsoft Agent Framework
  workflows (`agent-framework-core` 1.19.0, workflows only, no model providers). Plan nodes
  become executors that pass a small "step finished with this outcome" message; edges become
  conditional edges, joins become fan-in groups, approvals use the framework's request/response
  mechanism, and `max_steps` is its iteration limit. Node behaviour comes from the same shared
  module as the LangGraph adapter.
- It passes the **same 14 contract tests** as LangGraph. On this engine, edges into a join must
  be unconditional; `build` refuses a plan that breaks this instead of hanging.
- `test_same_spec_both_adapters.py` compiles one `agent.yaml` (parallel checks, a join, routing
  by size) and runs it on both engines: the final state and the set of finished nodes are
  identical, for both routes.
- `runtime_adapter.crewai` is a documented stub that refuses clearly, as the catalogue marks it.

## Why
A seam you have only used once is a guess. Running the same spec on two very different engines
and getting the same answer is the proof that the platform, not LangGraph, owns the behaviour,
so the engine can be swapped later.

## How to check it
`uv run pytest libs/agent-runtime/tests` — both adapters' contracts, the same-spec test and the
stub test pass.

## Risks and follow-ups
Durability on this engine is in memory until Step 18. The OpenAI Agents SDK, Pydantic AI and
Google ADK adapters are agent-loop frameworks first; ADR 0022 explains why they arrive with the
agent patterns in Step 13 rather than as workflow-only adapters now.
