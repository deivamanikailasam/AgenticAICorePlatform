# Refuse specs that use MCP servers outside the allowlist

**Step:** 07 · **Commit:** feat(compiler): refuse specs whose MCP tools come from unlisted servers · **Date:** 2026-10-15

## What changed
With the platform configuration, `compile_spec` now checks every `mcp://<server>/<tool>` in a
spec and refuses to compile if a server is not in `platform.yaml` `mcp.servers`, naming the
missing servers.

## Why
Catching it when the spec is compiled means a reviewer sees the problem in CI, long before an
agent tries to call the tool at runtime.

## How to check it
`uv run pytest libs/agent-compiler -k unlisted` passes; the error reads
"MCP server(s) not on the allowlist (platform.yaml mcp.servers): erp, rag-core".

## Risks and follow-ups
`invoice-triage` references `erp` and `rag-core`; `rag-core` arrives through the platform MCP
gateway in Step 8, and `erp` is a sample server for the reference app in Step 40.
