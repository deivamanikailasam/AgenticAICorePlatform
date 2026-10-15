# One MCP door for every tool: the platform MCP gateway

**Step:** 08 · **Commit:** feat(mcp-gateway): add the platform MCP server with Keycloak auth, OPA and audit · **Date:** 2026-10-15

## What changed
A new service, `services/mcp-gateway`, is an MCP server (Streamable HTTP, `127.0.0.1:8090/mcp`)
that any MCP client — Claude Code, MCP Inspector, another team's agent — can use:

- it exposes `platform__calculator`, `platform__clock`, `platform__word_count`,
  `rag-core__search`, and the tools of allowlisted external servers listed in `MCP_GATEWAY_PROXY`;
- every request must carry a Keycloak token (otherwise HTTP 401 with OAuth discovery metadata);
- each call: the token is verified → tenant and groups come from the token → **OPA decides** →
  the platform tool executor runs the tool → the decision and outcome are **audited**
  (hash-chained). Refused calls never reach the tool;
- `rag-core__search` runs with the caller's own permissions through token exchange.

It runs as a hardened container (read-only filesystem, no capabilities, 384 MiB) via `just up`,
built from one generic service Dockerfile.

## Why
Many doors are hard to guard. With one gateway, every tool call from every client passes the same
identity check, the same policy and the same audit — like a building where all visitors come
through one reception desk.

## How to check it
`just up` → `curl 127.0.0.1:8090/healthz` returns `{"status":"ok"}` and an unauthenticated
`POST /mcp` returns 401. With the stand-in: `uv run pytest -m integration services/mcp-gateway`
— alice (acme finance) gets only finance documents, bob (acme engineering) only engineering ones,
gina (globex) only globex's; every call is audited with an `allow` decision.

## Risks and follow-ups
Audit goes to a local JSONL hash chain until the Project 0 audit adapter (Step 31). Tenants are
derived from Keycloak group paths (`/acme/finance`), an assumption to align with Project 0
(VERIFY-ON-MAC). The platform's tenant/data-class/rate-limit policy grows in Step 10.
Also: pytest now uses --import-mode=importlib so test files with the same name in different packages no longer collide.
