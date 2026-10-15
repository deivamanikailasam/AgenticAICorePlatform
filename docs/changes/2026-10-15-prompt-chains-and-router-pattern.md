# Prompt chains with checkpoints, and a router pattern

**Step:** 15 · **Commit:** feat(agent-compiler): add prompt chains with validation gates and the router pattern · **Date:** 2026-10-15

## What changed
- **Validation gates.** An LLM step can carry `config.gate`: a JSON Schema, simple checks
  (`min_length`, `max_length`, `pattern`, `not_pattern`, `one_of`, `equals`, optionally at a
  `path`) and/or a named function. Output that fails is sent back to the model with the reasons,
  at most `max_retries` times (default 1); then the step fails, so an `on_error` edge can take
  over (for example, hand the case to a person).
- **`pattern: prompt_chain`** compiles a straight line of LLM, function, tool and approval steps.
  The compiler refuses a chain whose LLM step feeds the next step without a gate, a chain that
  branches other than on `on_error`, and a step with an `on_error` exit whose next edge is not
  `on_ok` (an unconditional edge would also run after an error).
- **`pattern: router`**: the entry must be a router node and every route it can choose must lead
  somewhere; handlers can be any node, including agents.
- Gate functions are resolved like other spec functions; nothing else in a gate runs as code.

## Why
A chain is only as good as its weakest step: a gate stops a bad intermediate answer before the
next step builds on it, which is cheaper than finding it at the end. The router pattern makes
"classify, then hand off" a first-class, checked shape.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_gates.py libs/agent-compiler/tests/test_chain_and_router_patterns.py`
— a gate repairs a bad currency then passes the value on; a gate that keeps failing takes the
`on_error` path after exactly two model calls; the router sends a maths question to an agent
with a calculator and a greeting to an LLM step; the compile-time checks refuse bad shapes. Both
runtime adapters.

## Risks and follow-ups
Each gate retry is another model call; keep `max_retries` low. Gates check form, not truth: a
well-formed wrong answer passes unless a check or function catches it.
