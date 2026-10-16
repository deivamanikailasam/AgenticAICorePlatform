# MCP client refuses a direct call to a tool off the server's allowlist

**Step:** 33 · **Commit:** fix(agent-tools): refuse MCP tools off the allowlist on direct calls · **Date:** 2026-10-16

## What changed
`McpClientPool.call` now refuses a tool that isn't in the server's `allowed_tools` before connecting. It raises `McpServerRefusedError`, and the attempt is traced and audited as a failed `mcp.call`.

## Why
The new MCP tool contract (Step 33) found the gap. Only allowlisted tools are registered, so agents could not reach the others through the executor. But a direct `pool.call("mcp://legacy/wipe_disk", …)` was forwarded to the server, even though `wipe_disk` is not allowlisted. The allowlist is a security control (least-privilege tools), so it now holds at the client as well as at registration.

## How to check it
`uv run pytest libs/agent-tools/tests/test_mcp.py -k allowlist`: the call is refused, it is audited with `ok: false`, and no connection is made (no protocol version is recorded).

## Risks and follow-ups
None. Servers without an allowlist keep their previous behaviour: everything they offer can be called.
