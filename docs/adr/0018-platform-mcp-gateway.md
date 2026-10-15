# ADR 0018: Platform MCP gateway with Keycloak auth, OPA per call, token exchange and audit

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 08
- **Deciders:** repository owner

## Context
Step 8 exposes platform tools and Project 0's `rag-core` search over MCP to any MCP client, and
proxies external servers so all tool traffic has one policy and audit point.

## Decision
1. **Service.** `services/mcp-gateway`: the MCP SDK's low-level `Server` (dynamic tool list) on
   Streamable HTTP at `/mcp`, with DNS-rebinding protection and an allowed-hosts list.
2. **Authentication.** Bearer tokens from Project 0's Keycloak, verified locally (JWKS, issuer,
   expiry, audience `agent-platform`). Unauthenticated requests get 401 with RFC 9728 protected
   resource metadata so standard MCP clients can discover how to log in.
3. **Identity → context.** Tenant = top level of the user's Keycloak group paths (`/acme/finance` →
   `acme`); several tenants in one token is refused. Subject (user, client, groups, roles) goes to
   OPA. *Assumption to align with Project 0 (VERIFY-ON-MAC).*
4. **Authorisation.** `data.agent.tools.decision` in OPA for every call; fail closed. Baseline
   rules now; Step 10 extends them.
5. **On behalf of the user.** Downstream calls (rag-core) use RFC 8693 token exchange to a token
   restricted to that service's audience; the user's raw token is never forwarded and the
   platform's service account never acts for a user. Keycloak 26 standard token exchange with an
   optional audience client scope per downstream service.
6. **Naming.** `platform__<tool>`, `rag-core__search`, `<server>__<tool>` for proxied servers.
7. **Audit.** One hash-chained event per call with the policy decision and outcome (JSONL until
   the Project 0 audit adapter in Step 31).
8. **rag-core contract (assumed).** `POST /v1/search {query, k, architecture}` →
   `{results: [{id, title, snippet, score, source, citation}]}`; tested against a test double of
   that contract. *VERIFY-ON-MAC.*

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| FastMCP-style static tools | The tool set is dynamic (proxied servers) |
| Token passthrough to rag-core | Anti-pattern in the MCP security guidance; tokens usable elsewhere |
| Service account for rag-core | Ignores the user's permissions (OWASP ASI03) |
| Policy checks inside each tool | Inconsistent and invisible (ADR 0009) |
| Separate endpoint per virtual server | More lifespans to manage; prefixes are enough today |

## Consequences
External clients get one stable MCP endpoint. Project 0's realm needs group claims, the
downstream clients and token exchange enabled for `agent-platform` (provisioned by Step 1's
script, extended here).

## Verification
`services/mcp-gateway/tests/test_gateway.py` (offline) and `test_gateway_integration.py`
(stand-in Keycloak, OPA, rag-core double).
