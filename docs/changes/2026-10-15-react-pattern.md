# `pattern: react` works from agent.yaml, with golden tool tasks

**Step:** 13 · **Commit:** feat(agent-compiler): add the react pattern and golden tool tasks · **Date:** 2026-10-15

## What changed
- `pattern_builder.react` turns a spec (or an `agent` node in a hybrid graph) into an agent-loop
  node plus an approval node it can route to. A whole-spec agent gets every spec tool and the
  `default` model; an agent node gets its own `tools`, `model`, `config` and `max_iterations`
  (default: `budgets.max_steps`). The compiler passes which tools need approval.
- `agent_compiler.run` plugs in the agent-loop executor (model client plus the authorising tool
  executor and its registry).
- `evals/golden/react-tools/` holds 8 golden tool tasks (calculator and word counter).
- New tests run `pattern: react` specs on **both** adapters: all 8 golden tasks through native
  tool calls and again through the text fallback, the run stopping at `max_steps` with status
  `stopped`, and an approval pause and resume that runs the approved write exactly once.
- `agents/invoice-triage` now compiles: its `resolve_exceptions` agent node becomes a loop with
  `get_invoice` and `search`.

## Why
A spec author should only have to say "this is an agent, with these tools and limits". The
golden tasks are a small, fixed exam that every change to the loop must keep passing.

## How to check it
`uv run pytest libs/agent-compiler/tests/test_react_agent.py`. On the Mac,
`uv run pytest -m mac libs/agent-compiler/tests/test_react_agent.py -s` runs the same 8 tasks
against Project 0's local model and prints how many it solved.

## Risks and follow-ups
In CI the golden tasks use a deterministic stand-in model: they prove the plumbing, not model
skill. The real-model score is VERIFY-ON-MAC and will be reported with its dataset size (8).
