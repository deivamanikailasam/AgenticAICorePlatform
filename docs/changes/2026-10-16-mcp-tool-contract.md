# The MCP tool contract, against real MCP servers

**Step:** 33 · **Commit:** test(agent-tools): MCP tool contract against real servers · **Date:** 2026-10-16

## What changed
`agent_tools.mcp.contract.McpToolContract` is a reusable test suite for any MCP server the platform allows. Subclass it with the server's configuration and one example call per tool. It checks that:
- every allowed tool is discovered, namespaced (`server__tool`, `mcp://server/tool`) and has a valid JSON Schema;
- every tool has an example, so a new tool can't go untested;
- each example succeeds through the platform executor, with JSON output that matches the tool's output schema;
- arguments that break the schema are refused before the server sees them;
- side effects follow the platform's declarations, and an untrusted server can't claim to be read-only;
- tools off the allowlist are neither offered nor callable.

It runs against:
- the invoice-triage ERP demo server and the two local test servers (modern and legacy protocol), over stdio with no network, in `just test`;
- the filesystem server that `platform.yaml` allows, in `-m integration`, because `npx` downloads it.

## Why
Step 33 asks for "tool contract tests against MCP servers". The generic `ToolContract` only checks a tool object; this checks what the platform relies on from a real server, through the real client. Its first run found the allowlist gap fixed in the previous commit.

## How to check it
- `uv run pytest libs/agent-tools/tests/test_mcp_contracts.py`: 18 tests (3 servers × 6 checks).
- `-m integration`: the filesystem server's 6 checks.

## Risks and follow-ups
The Project 0 rag-core server and the platform's own MCP gateway are not under this contract here: one needs Project 0 (VERIFY-ON-MAC), and the other needs Keycloak tokens. Adding them is a subclass each.
