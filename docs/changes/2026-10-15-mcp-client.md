# Connect to MCP tool servers safely: negotiate, discover, call, trace, audit

**Step:** 07 · **Commit:** feat(tools): add the MCP client pool with negotiation, OAuth, tracing and audit · **Date:** 2026-10-15

## What changed
`McpClientPool` (in `agent-tools`) is how the platform uses external MCP tool servers:

- **Allowlist only**: a server not in `platform.yaml` is refused.
- **Protocol negotiation**: tries the current stateless 2026-07-28 revision, and falls back to the
  2025-11-25 handshake for older servers — including ones that hang up when probed.
- **Discovery and naming**: each tool becomes `mcp://<server>/<tool>`, shown to models as
  `<server>__<tool>`; `allowed_tools` limits what is exposed.
- **Trust**: read-only/destructive hints are believed only from trusted servers; otherwise the
  platform owner's `side_effects` list decides, and the default is `write` (so a key is required).
  Untrusted tool descriptions are clipped.
- **OAuth for remote servers**: client-credentials tokens from Keycloak, discovered through the
  server's protected-resource metadata; secrets resolved at call time, tokens kept in memory only.
- **Every call** produces an `execute_tool <tool>` span (with the negotiated protocol version) and
  a hash-chained audit event in which the arguments are hashed, never stored.
- **Safe environments for stdio servers**: only proxy/CA variables are passed through.
- `KeycloakTokenVerifier` checks Keycloak JWTs (signature, issuer, expiry, optional audience) for
  platform-hosted MCP servers (used again by the gateway in Step 8).

## Why
MCP is the standard plug for tools (ADR 0006). Plugs from the internet need a fuse box: only
approved sockets, the right voltage (protocol version), and a meter on every circuit (traces and
audit).

## How to check it
`uv run pytest libs/agent-tools/tests/test_mcp.py` (offline: negotiation, trust, allowlist,
trace, audit). With the stand-in: `uv run pytest -m integration libs/agent-tools/tests/test_mcp_integration.py`
— the official filesystem server (stdio, 2025-11-25) and an OAuth-protected HTTP server
(2026-07-28) are discovered, called, traced and audited; an unlisted server is refused.

## Risks and follow-ups
Audit events go to an `AuditSink`; writing them into Project 0's audit log is Step 31. The
integration test downloads the filesystem server with `npx`, so CI needs npm registry access.
