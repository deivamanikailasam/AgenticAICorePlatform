# ADR 0035: A2A gateway with signed Agent Cards; remote agents as allowlisted tools

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 24
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 24 asks for:
- publishing chosen agents as A2A agents with signed Agent Cards (skills, auth requirements,
  endpoints);
- calling remote A2A agents as tools;
- the task lifecycle, streaming and push notifications;
- Keycloak-issued credentials;
- OPA on outbound and inbound calls;
- treating remote messages as untrusted input.

The spec's default is A2A v1.x over JSON-RPC or HTTP+JSON, with gRPC as the alternative.

Done when: an agent from another framework calls a Project A agent over A2A and the reverse,
and an unsigned or unlisted remote agent is refused. ADR 0007 already limits A2A to calls
across a boundary; agents in one process talk through the runtime.

## Versions (checked 2026-10-15, 14-day cooldown per ADR 0022)
| Package | Pinned | Released | Note |
|---|---|---|---|
| `a2a-sdk[http-server,signing]` | 1.2.1 | 2026-09-30 | Official Python SDK for A2A v1.x; 1.2.2 is inside the cooldown |
| `grpcio` | 1.84.0 | 2026-09-14 | gRPC binding (client and server) |

## Decision
1. **A separate `a2a-gateway` service.** It publishes the agents whose spec sets
   `interop.a2a_publish`.
   - Each agent gets its own root, `/agents/<name>`, with a card, JSON-RPC and HTTP+JSON.
   - gRPC is served for one chosen agent on its own port.
   - Each agent's routes sit in their own mount, and the SDK's path-tenant route is dropped:
     the tenant comes from the token, and one agent's routes can never serve another's path.
   - A task runs through the run API with the caller's own token, so there is still one policy
     path.
2. **Signed cards.** Every card is signed ES256 (JWS over the canonical card, as the SDK
   implements it) with the platform key. The key comes from the secret store (OpenBao in
   Step 29); the gateway will not start without it. Cards are public, so callers can discover
   how to authenticate.
3. **Inbound gate.** The gateway first verifies the Keycloak bearer token (401 if it fails).
   OPA then decides `a2a.inbound`:
   - `tool` is the agent and `allowed_tools` is the published agents;
   - the agents' kill switch applies;
   - a denial is 403, an unpublished agent is 404;
   - the decision is audited.

   gRPC uses the same gate in a server interceptor.
4. **Remote agents are tools only from an allowlist.** `platform.yaml` `a2a.agents` lists each
   remote agent with its pinned card keys (kid → PEM).
   - An `a2a://<name>` ref that is not listed fails when the run is built.
   - A card without a valid signature from a pinned key is refused before any message is
     sent.
   - `jku` (keys fetched from a URL) is not trusted: anyone who controls DNS or the card host
     could supply their own key.
5. **Outbound calls are ordinary tool calls.** OPA (`tool.call`, tag `external`), grants, rate
   limits and audit all apply. The default side effect is `write`, because remote agents act.
   Credentials follow the agent's `auth`:
   - `on_behalf_of` (RFC 8693 exchange of the user's token), which never falls back to a
     service identity;
   - client credentials;
   - a bearer token from the secret store;
   - none.
6. **Remote replies are data.** The reply is a tool output (`{agent, task_id, state, text}`)
   and goes through the same untrusted handling as any external tool result.
7. **Push notifications** only to allowlisted hosts over HTTPS (plain HTTP to loopback only),
   so a caller cannot use them for SSRF.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Pydantic AI `to_a2a()` (FastA2A) for publishing | Ties publishing to one engine; FastA2A has no card signing or Keycloak/OPA hooks |
| Trust keys from the card's `jku` | Convenient key rotation, but an attacker controlling the card host controls the key. Pinning wins until a trusted registry exists (Step 25) |
| A2A inside the run API process | Fewer services, but mixes public interop with the internal API and its blast radius |
| Persistent task store now | Needed for gateway restarts mid-task; runs are already durable in agent-api, so it waits |

## Consequences
- Partner key rotation is a config change.
- A2A task ids live in gateway memory: a gateway restart loses the mapping from task to run.
  The runs themselves continue.
- Streaming reports task states and the final artifact, not token-by-token text.
- An A2A caller cannot approve its own pending action. Approvals stay with the platform's
  approvers (run API or UI), and the task shows input-required until then.

## Verification
- `services/a2a-gateway/tests/test_a2a.py`, 10 tests:
  - Pydantic AI → Project A over A2A;
  - Project A → Pydantic AI as `a2a://pyd-helper`, with a hostile reply returned as data and
    the call audited;
  - streaming lifecycle;
  - a run waiting for approval reports input-required;
  - each agent served on its own routes;
  - a card checked against the wrong key is refused;
  - 401, 403 and 404 cases;
  - push URL allowlist;
  - gRPC allowed and denied.
- `libs/agent-tools/tests/test_a2a_tool.py`, 6 tests: signed call with a secret-store token;
  unsigned card, rogue key and unpinned kid refused with no message sent; `on_behalf_of`
  without a user token refused; at least one pinned key required.
- `services/agent-api/tests/test_a2a_refs.py`: an unlisted agent refused at build time; a listed
  agent registered.
- `policies/agent/tools/decision_test.rego`: four `a2a.inbound` tests.
