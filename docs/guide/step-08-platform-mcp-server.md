# Step 8 · Platform MCP server (the gateway)

## 1. At a glance

| | |
|---|---|
| **Goal** | One MCP endpoint for every tool, with identity, policy and audit on every call |
| **Time** | About a day |
| **You end up with** | `services/mcp-gateway`, the OPA decision point and baseline tool policy, RFC 8693 token exchange, the `rag-core/search` tool, a hardened container, stand-in realm users |
| **New words** | gateway, bearer token, JWKS, protected resource metadata, token exchange, on-behalf-of, audience |

## 2. The big idea

A building with one reception desk. Every visitor (an MCP client) shows a badge (a Keycloak
token), the desk checks the visitor list (OPA), issues a single-room key card for the room they
need (token exchange), and writes the visit in the logbook (audit). Visitors never wander into
rooms on their own.

> *Analogy:* a hotel key card that opens one room for one night is safer than lending someone
> your master key — that is the difference between token exchange and forwarding a user's token.

## 3. What we're doing

1. Add the OPA decision point (fail closed) and a baseline tool policy with Rego tests.
2. Enable token exchange for `agent-platform` and create audience scopes per downstream service.
3. Add `rag-core/search` as a tool that runs only on behalf of a user.
4. Build the gateway: Keycloak auth, OPA on every call, executor, audit; proxy allowlisted servers.
5. Run it as a hardened container with `just up`.

## 4. Why we're doing this

Without a gateway, each client would talk to each tool directly, and each tool would need its own
login, permission checks and logging — which in practice means some don't. The OWASP agentic list
calls out tool misuse (ASI02) and identity and privilege abuse (ASI03): a tool running with a
powerful service account can leak another user's documents.

> *Why this matters:* the Done-when for this step is a security property — a user sees **only**
> the documents they may read — and it is checked with three different users across two tenants.

## 5. Key ideas

- **Bearer token / JWT.** A signed statement from Keycloak: who you are, which groups, until when.
  The gateway checks the signature with Keycloak's public keys (JWKS).
- **Protected resource metadata.** The gateway tells clients which login server issues its tokens,
  so standard MCP clients can log in by themselves.
- **Fail closed.** If OPA can't answer, the answer is no.
- **Token exchange (on-behalf-of).** Swap the user's token for one that only `rag-core` accepts and
  that still names the user.
- **Tenant from groups.** `/acme/finance` means tenant `acme`, group `finance`.

## 6. How to do it

```bash
just up                                   # builds and starts mcp-gateway on 127.0.0.1:8090
curl -s 127.0.0.1:8090/healthz            # {"status":"ok"}
curl -s -o /dev/null -w "%{http_code}\n" -X POST 127.0.0.1:8090/mcp   # 401
```

Connect an MCP client (e.g. MCP Inspector or Claude Code) to `http://127.0.0.1:8090/mcp`; it will
be sent to Keycloak to log in. Then call `rag-core__search` with `{"query": "refund policy"}`.

Tests (CI / cloud: stand-in with test users alice, bob, gina):

```bash
just provision                            # includes OPA policies and token exchange setup
uv run pytest services/mcp-gateway                        # offline
uv run pytest -m integration services/mcp-gateway         # end to end
```

See `docs/architecture/mcp-gateway-sequence.md` for the full sequence.

## 7. Benefits

- One place to authenticate, authorise and audit every tool call.
- Users get exactly their own document permissions, across tenants.
- Standard MCP clients can connect without custom code.
- Refused calls never reach the tool.

## 8. Drawbacks and risks

- The gateway is on the critical path of every tool call; it must be replicated in production
  (Step 38).
- Token exchange depends on Keycloak configuration in Project 0's realm (VERIFY-ON-MAC).
- The rag-core API shape is assumed until checked on the Mac.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Gateway | Direct client-to-server | Trusted internal tools on the same host, behind an ADR |
| Token exchange | Forward the user token | Never |
| OPA | Cerbos (Step 10 adds it as an option) | Teams preferring YAML policies |
| Group-path tenants | A `tenant` claim | If Project 0 already issues one |

## 10. Which approach fits which situation

- **External MCP clients (developers, other teams):** connect to the gateway's `/mcp`.
- **Platform agents:** use the same gateway (Step 13 onwards), so their calls share policy and audit.
- **A new downstream service that needs user permissions:** add it to
  `infra/provision/keycloak.yaml` and call it through `TokenExchanger`.

## 11. Common mistakes

- Forgetting `just provision-policies` after editing Rego: OPA still has the old rules.
- A token from a client without the `agent-platform` audience: the gateway rejects it.
- A user in two tenants: refused on purpose; pick one tenant per session.

## 12. Done when

| Check | Status | Evidence |
|---|---|---|
| An external MCP client can call `rag-core/search` with a user token and only sees permitted documents | ✅ stand-in · **VERIFY-ON-MAC** | `test_users_only_see_documents_they_may_read`: alice → fin-001, fin-002; bob → eng-001; gina → glx-001 (real Keycloak tokens, token exchange, OPA) against a test double of rag-core |
| Keycloak auth and OPA checks on every call | ✅ | 401 without token; `test_refused_calls_never_reach_the_tool`; audit shows the decision |
| Proxies external servers (one policy and audit point) | ✅ | `MCP_GATEWAY_PROXY`; proxied calls use the same executor, policy and audit path |
| Gateway runs as a service | ✅ stand-in | `just up` → healthy `mcp-gateway` container |
| ADR | ✅ | ADR 0018 |

## 13. Recap

Step 8 built the platform's single door for tools: an MCP gateway that verifies Keycloak tokens,
derives tenant and groups, asks OPA (failing closed) on every call, executes tools through the
platform executor, calls rag-core with an audience-restricted exchanged token so users see only
their own documents, audits every decision and outcome, and runs as a hardened container.
