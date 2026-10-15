# ADR 0017: MCP client — allowlist, negotiation fallback, trust model, OAuth and task-owned connections

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 07
- **Deciders:** repository owner

## Context
External MCP servers are the main way agents gain capabilities, and the main supply-chain and
tool-misuse risk (OWASP ASI02, ASI04). The current revision is 2026-07-28 (stateless); many
servers still speak the 2025-11-25 handshake.

## Decision
1. **SDK.** The official MCP Python SDK, pinned `>=2.3.0,<2.4` (2.3.0 on PyPI, 2026-10-15). Its
   `Client` negotiates `auto` (probe `server/discover`, else handshake).
2. **Fallback.** Some older servers hang up when probed; on a failed `auto` connection the client
   reconnects with `legacy` (2025-11-25 handshake). Servers can pin `legacy` or `2026-07-28`.
3. **Allowlist.** Only servers in `platform.yaml` `mcp.servers` are reachable, enforced in the
   client and at compile time (and in the gateway from Step 8).
4. **Trust.** Annotations are believed only for `trusted: true` servers. Otherwise side effects
   come from the owner's `side_effects` map or default to `write` (idempotency key required).
   Untrusted descriptions are clipped to 1,024 characters and treated as untrusted text (Step 27
   scans them for injection).
5. **Naming.** `mcp://<server>/<tool>` refs; model-safe names `<server>__<tool>` (≤ 64 chars,
   hashed suffix if longer).
6. **Auth.** Remote servers: OAuth 2.0 client credentials discovered via RFC 9728 protected
   resource metadata, with the issuer pinned in config (SDK `ClientCredentialsOAuthProvider`);
   tokens held in memory only; secrets as `${secret:…}` references resolved at connect time.
7. **Environment.** Stdio servers get the SDK's minimal environment plus an allowlist (proxy/CA
   variables by default), never the platform's full environment.
8. **Connections are owned by a task.** Each connection runs in its own asyncio task so anyio
   cancel scopes are entered and exited by the same task; any request task may use it.
9. **Observability.** Every call: span `execute_tool <tool>` with `gen_ai.*` and `mcp.*`
   attributes including the negotiated `mcp.protocol.version`; an audit event with the SHA-256 of
   the arguments, never the arguments themselves.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Our own MCP client | Re-implements a moving protocol; the SDK tracks the spec |
| Trust all annotations | A malicious server could label a destructive tool read-only |
| Pass the full environment to stdio servers | Leaks API keys and DSNs to third-party code |
| Static bearer tokens only | No rotation; OAuth client credentials are the MCP-recommended path |
| Re-connect per call | Simple but slow for stdio servers; task-owned pooled connections instead |

## Consequences
Platform owners curate the allowlist and side-effect maps. CI integration tests download the
official filesystem server with npx.

## Verification
`libs/agent-tools/tests/test_mcp.py` (offline) and `test_mcp_integration.py` (stand-in Keycloak,
real filesystem server, OAuth-protected HTTP server).
