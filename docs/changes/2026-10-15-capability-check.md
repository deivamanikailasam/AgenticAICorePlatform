# Refuse, at compile time, a model that can't call tools for a tool-using node

**Step:** 05 · **Commit:** feat(compiler): check model capabilities per role at compile time · **Date:** 2026-10-15

## What changed
When `compile_spec` is given the platform configuration, it resolves each agent or LLM node's
model role to a gateway alias and refuses to compile if a node that uses tools would get a model
without tool calling. The plan also records which alias each role resolved to
(`model_aliases`), so a run can always say which model it was meant to use.

## Why
Finding out mid-run that the model can't call tools wastes money and confuses users. It is like
checking a driver holds the right licence before handing over the truck keys.

## How to check it
`uv run pytest libs/agent-compiler -k capability` passes; the error reads
"node 'resolve_exceptions' uses tools but model '…' (role 'planner') has no tool calling".

## Risks and follow-ups
The check trusts `platform.yaml`'s capability table; it must be kept accurate (VERIFY-ON-MAC).
