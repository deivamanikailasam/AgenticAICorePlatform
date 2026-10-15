# Add platform.yaml: which model each role uses, and what each model can do

**Step:** 05 · **Commit:** feat(spec): add platform.yaml with model roles, capabilities and AUD rate · **Date:** 2026-10-15

## What changed
`platform.yaml` at the repo root holds platform-wide settings:

- the gateway address and per-role key naming (`LITELLM_KEY_PLANNER`, …);
- the six model **roles** (default, planner, executor, router, judge, summariser) mapped to
  LiteLLM aliases (`local-qwen-fast`, `local-qwen-quality`);
- each alias's **capabilities** (tool calling, structured output, streaming, context window);
- the **USD→AUD rate** with its source and date (1.4434, from AUD/USD 0.6928 on 2026-10-01).

`agent_spec.platform.load_platform()` validates it; its schema and reference page are generated
into `docs/reference/` like `agent.yaml`'s.

## Why
Specs say *what a model is for* (a planner), not *which model* — like a job title on an org
chart rather than a person's name. When the person changes, the chart stays the same.

## How to check it
`uv run python -c "from agent_spec.platform import load_platform; print(load_platform().alias_for('planner'))"`
prints `local-qwen-quality`.

## Risks and follow-ups
Capability values for the local Qwen models are assumptions until checked against Project 0's
model cards (VERIFY-ON-MAC). The FX rate is configuration, not a measurement; refresh monthly.
`just typecheck` now skips source folders that contain no Python files yet.
