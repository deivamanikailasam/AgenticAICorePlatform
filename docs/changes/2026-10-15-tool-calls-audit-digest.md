# Tool and MCP calls audit the arguments' digest and a redacted copy

**Step:** 31 · **Commit:** feat(agent-tools): tool calls audit a digest and redacted arguments · **Date:** 2026-10-15

## What changed
- Every `tool.call` audit event now carries `arguments_digest` and the redacted `arguments` from `agent_audit.redact`.
- A call to a remote agent (`a2a://` ref) also writes an `a2a.call` event.
- The MCP client's transport record is now the catalogued `mcp.call` type and carries the digest.

## Why
Step 31 asks that the trail show what each tool was asked to do without storing secrets or personal data. The digest is the same one an approval binds to, so an auditor can match "approved X" to "ran X" exactly.

## How to check it
- `uv run pytest libs/agent-tools libs/agent-audit -k "argument or audit"`.
- `services/agent-api/tests/test_audit_trail.py` checks a real paid invoice: the `pay` call's digest equals the approval's.

## Risks and follow-ups
Redaction is pattern-based. A secret in a field with an innocent name and no recognisable pattern would be kept, truncated to 200 characters. The digest is still exact.
