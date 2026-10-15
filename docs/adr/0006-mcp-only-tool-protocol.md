# ADR 0006: MCP (revision 2026-07-28) is the only tool integration protocol

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 01
- **Deciders:** repository owner

## Context
Tools come from many places: in-process functions, internal services, external MCP servers,
remote agents. Each path would otherwise need its own permission and audit logic.

## Decision
The Model Context Protocol is the tool contract. The current stable revision is 2026-07-28
(stateless; each request carries its protocol version and capabilities), confirmed on
2026-10-15. The client negotiates down to 2025-11-25 for older servers. In-process tools are
wrapped in the same `Tool` contract so one permission (OPA) and audit path covers all tools.
MCP Python SDK 2.3.0 is current on PyPI (pinned in Step 7).

## Alternatives considered
| Option | Why not |
|---|---|
| Framework-native tools (LangChain tools etc.) | Tied to one framework; no standard discovery |
| OpenAPI-only tools | Fine for HTTP APIs, but no standard for discovery, annotations or streaming to agents |

## Consequences
Portability across agents and clients (Claude Code, Inspector). MCP's own security model
(annotations are hints, not guarantees) must be wrapped by platform policy.

## Verification
Steps 7–8: two external servers discovered, called, traced and audited; unlisted servers refused.
