# Give every tool a model-safe name and a source reference

**Step:** 06 · **Commit:** feat(contracts): add ToolSpec.ref and restrict tool names to model-safe characters · **Date:** 2026-10-15

## What changed
A tool now has two identifiers: `name`, what the model sees (letters, digits, `_` and `-`, up to
64 characters, as OpenAI-style and MCP function names require), and `ref`, where it comes from
(`local://platform/calculator`, `mcp://erp/get_invoice`).

## Why
Models reject function names with `://` or `/`. Keeping the source separately means the trace,
the card and the policy engine still know exactly which server a call went to.

## How to check it
`uv run pytest libs/agent-contracts` passes; `ToolSpec(name="mcp://x")` raises a validation error.

## Risks and follow-ups
None.
