# Define the sockets every part plugs into

**Step:** 02 · **Commit:** feat(contracts): add shared models, 23 interfaces and the component registry · **Date:** 2026-10-15

## What changed
A new library, `agent-contracts`, defines the *shape* of every switchable part of the
platform: 23 interfaces (model client, tool, tool registry, tool executor, pattern builder,
runtime adapter, checkpointer, durable executor, memory store, memory policy, context manager,
guardrail, policy decision point, approval gate, budget controller, loop detector, planner,
router, critic, telemetry emitter, audit sink, evaluator, simulator) and the Pydantic models
they exchange (messages, tool calls, plans, run events, checkpoints, memory items, policy
decisions, approvals, budgets, audit records, eval scores).

A registry maps a slot and a name (`tool_executor` + `mcp`) to an implementation. Parts
register with `@register("slot", "name")` or a Python entry point, and configuration picks
them by name.

## Why
Standard sockets: once every lamp has the same plug, you can swap lamps without rewiring the
house. Here, swapping LangGraph for another engine, or OPA for Cerbos, means changing one name
in a config file, not the code that uses it.

## How to check it
`uv run pytest libs/agent-contracts` passes; `just components` runs (it lists nothing until
the next commit registers implementations).

## Risks and follow-ups
Interfaces are hard to change once many implementations exist; changes need an ADR and a
contract-test update.
