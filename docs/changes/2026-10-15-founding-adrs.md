# Write down the ten founding decisions

**Step:** 01 · **Commit:** docs(adr): add ADR template and founding ADRs 0001-0011 · **Date:** 2026-10-15

## What changed
Eleven short decision records explain the platform's biggest choices: a separate repo that
reuses Project 0, Python and Pydantic, a declarative `agent.yaml`, LangGraph behind an adapter,
two durability tiers, MCP for tools, A2A between agents, every model call through LiteLLM, OPA
for every tool call, OpenTelemetry GenAI traces, and why this build runs in a cloud container
with a stand-in for Project 0's services during tests.

## Why
A decision record is like the minutes of a planning meeting: a year from now someone will ask
"why didn't we just…?", and the answer, with the options that were rejected, is written down.

## How to check it
`ls docs/adr/` lists `0001`…`0011` plus `template.md`; `just adr "Test"` creates `0012-test.md`
(delete it afterwards).

## Risks and follow-ups
Version numbers in the ADRs were read from PyPI on 2026-10-15 and are pinned in the step that
installs each tool.
