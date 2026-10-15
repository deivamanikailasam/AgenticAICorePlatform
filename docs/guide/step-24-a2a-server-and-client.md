# Step 24 · A2A server and client

## 1. At a glance

| | |
|---|---|
| **Goal** | Let agents on other systems call ours, and let ours call theirs, safely |
| **Time** | About two days |
| **You end up with** | `services/a2a-gateway` (signed Agent Cards; JSON-RPC, HTTP+JSON, gRPC); `a2a://<agent>` tools from an allowlist in `platform.yaml`; OPA `a2a.inbound`; tests in both directions with Pydantic AI |
| **New words** | A2A, Agent Card, signed card, pinned key, push notification |

## 2. The big idea

A2A (Agent2Agent) is a shared language for agents. One agent sends another a task; the other
works on it and reports back. To be safe, both sides must know who they are talking to:
- our agents publish a signed "business card";
- we only talk to remote agents whose cards are signed by keys we wrote down in advance.

> *Analogy:* a company switchboard. Outsiders can look up the published directory (the Agent
> Card), but to be put through they must show a staff badge (a Keycloak token), and reception
> checks the visitor list (OPA). When we call out, we only dial numbers in our own address book
> and check the caller ID matches (the pinned key).

## 3. What we're doing

1. Add an `a2a:` section to `platform.yaml`:
   - `publish` (how we serve our agents);
   - `agents` (the remote agents we may call, each with pinned card keys).
2. Build the `a2a-gateway`. It serves every agent whose spec says
   `interop: { a2a_publish: true }`, each with a signed Agent Card.
3. Gate every call: a valid Keycloak token, then an OPA decision, then the run API runs the
   agent as the caller.
4. Make `a2a://<name>` a tool. Verify the remote card's signature before the first message, and
   send credentials the way the config says.
5. Test both directions with an agent built in Pydantic AI, plus the refusals.

## 4. Why we're doing this

Agents increasingly work across company and team boundaries. Without checks, anyone could
pretend to be a trusted partner agent, or call our agents without permission. Two OWASP agentic
risks apply directly:
- **ASI04, supply chain:** an unknown or impersonated agent;
- **ASI07, insecure inter-agent communication.**

Treating a remote agent's reply as data, not instructions, stops a hostile partner from steering
our agent.

> *Watch out:* a signature only proves who signed the card. It says nothing about whether the
> agent behind it is honest. That is why replies stay untrusted, even from a correctly signed
> agent.

## 5. Key ideas

- **Agent Card.** A JSON file at `/.well-known/agent-card.json`. It lists:
  - skills;
  - endpoints per binding (JSON-RPC, HTTP+JSON, gRPC);
  - the security scheme (Keycloak OpenID Connect).
- **Signing and pinning.** We sign our cards with ES256. For each remote agent, `platform.yaml`
  lists the public key IDs we accept. A card with no signature, or signed by any other key, is
  refused before we send anything.
- **One policy path.** The gateway authenticates the caller and asks OPA once (`a2a.inbound`).
  The run API then does everything else: grants, approvals, budgets, audit and per-tool policy.
- **Outbound is a tool call.** Calling a remote agent goes through the tool executor:
  - OPA with the tag `external`;
  - side effect `write` by default.

  Credentials are the user's exchanged token (`on_behalf_of`), client credentials, or a bearer
  token from the secret store.
- **Task lifecycle.** Run results map to task states:
  - waiting for approval or input → `input-required`;
  - success → `completed`, with the answer as an artifact;
  - failure → `failed`.

  Streaming clients see the same steps as they happen.
- **Push notifications.** Only to allowlisted hosts over HTTPS, so a caller cannot make us call
  internal addresses.

## 6. How to do it

Publish an agent:

```yaml
# agents/hello-workflow/agent.yaml
interop: { a2a_publish: true }
```

Allow a remote agent:

```yaml
# platform.yaml
a2a:
  publish:
    base_url: ${A2A_PUBLIC_URL:-http://127.0.0.1:8092}
    key_id: agent-platform-1
    signing_key: ${secret:agent-platform/a2a#signing_key}
    push_allowed_hosts: [hooks.partner.example]
  agents:
    - name: pyd-helper
      url: https://agents.partner.example/helper
      card_keys: { partner-2026: "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n" }
      auth: { kind: on_behalf_of, audience: partner-helper }
```

Use it in a spec:

```yaml
tools:
  - ref: a2a://pyd-helper
graph:
  nodes:
    - { id: ask, type: tool, tool: pyd_helper, config: { args: { message: $input.question } } }
```

Create a dev signing key and run the gateway:

```bash
openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt > /tmp/a2a.pem
export A2A_SIGNING_KEY_DEV="$(cat /tmp/a2a.pem)"
just up    # a2a-gateway on 127.0.0.1:8092
curl -s http://127.0.0.1:8092/agents/hello-workflow/.well-known/agent-card.json
uv run pytest services/a2a-gateway/tests libs/agent-tools/tests/test_a2a_tool.py
```

## 7. Benefits

- Agents built with any framework that speaks A2A can use ours, and ours can use theirs.
- Impersonation is stopped by pinned keys, and unlisted agents are refused when the run is
  built.
- No second security model: the same Keycloak, OPA, grants and audit as everything else.
- JSON-RPC, HTTP+JSON and gRPC from one gateway.

## 8. Drawbacks and risks

- A partner's key rotation means a config change, since keys are pinned by hand.
- A2A task ids are held in the gateway's memory. A restart loses the mapping from task to run,
  though the run itself carries on.
- Streaming carries task states and the final answer, not word-by-word text.
- Another network service to run, secure and monitor.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Separate gateway | A2A inside the run API | Very small deployments where one fewer service matters more than isolation |
| Pinned card keys | Trust the card's `jku` key URL | A trusted agent registry vouches for keys (Step 25) |
| Official `a2a-sdk` | Pydantic AI `to_a2a()` (FastA2A) | Publishing a single Pydantic AI agent quickly, without card signing or platform policy |
| JSON-RPC / HTTP+JSON | gRPC | High-volume calls between services that already run gRPC |

## 10. Which approach fits which situation

- **A partner company's agent:** list it with pinned keys and `on_behalf_of`, so it sees only
  what the user may see.
- **Another internal platform:** client credentials, and a narrow OPA rule for its client ID.
- **A public read-only agent:** `auth: none`, `side_effect: read` only if it truly cannot
  change anything.
- **Agents in the same process:** don't use A2A; use a team pattern (Steps 22–23).

## 11. Common mistakes

- Publishing every agent "just in case". Publish only what a partner needs.
- Accepting a card's own key URL as proof of identity.
- Marking a remote agent `read` because "it only answers questions". It can still act on its
  side.
- Following instructions found in a remote agent's reply.
- Leaving `push_allowed_hosts` wide open.
- Letting the remote caller approve its own pending action. Approvals stay with the platform's
  approvers.
- Serving several agents from one set of routes. One agent's catch-all route can quietly take
  another agent's calls, which is why each agent has its own mount.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| An agent built with a different framework calls a Project A agent over A2A | ✅ | `test_a_pydantic_ai_agent_calls_a_project_a_agent_over_a2a`: a Pydantic AI agent's tool calls `capital-desk` through the gateway as alice (token checked, OPA `a2a.inbound` allowed) and gets "Canberra." |
| A Project A agent calls an agent from another framework | ✅ | `test_a_project_a_agent_calls_a_pydantic_ai_agent_as_a_tool`: `a2a://pyd-helper`, served with `a2a-sdk` and a signed card, answers. Its hostile reply is returned as data; the call is audited with OPA's `allow`; the partner received only the configured token |
| An unsigned or unlisted remote agent is refused | ✅ | Unsigned card, rogue key and unpinned kid are refused with no message sent; an unlisted `a2a://` ref fails at build time |
| Signed Agent Cards (skills, auth, endpoints) | ✅ | Card test: signature present; JSON-RPC and HTTP+JSON endpoints, plus gRPC when enabled; Keycloak scheme; skill per agent; unpublished agents 404 |
| OPA on inbound and outbound calls; Keycloak credentials | ✅ | 401 without or with a forged token, 403 on policy denial (also over gRPC), audit of inbound decisions; outbound goes through `tool.call`; `on_behalf_of` never falls back to a service identity; 4 rego tests |
| Task lifecycle, streaming, push | ✅ | Streaming test (working → artifact → completed); a run waiting for approval reports input-required with "Approve calculator?"; push URLs limited to allowlisted HTTPS hosts |
| gRPC binding | ✅ | `test_grpc_binding_is_gated_like_http` |
| ADR | ✅ | ADR 0035 |

VERIFY-ON-MAC: fetch the `hello-workflow` card from the compose service with a real signing key,
and call it with a Keycloak token from Project 0.

## 13. Recap

The `a2a-gateway` publishes chosen agents with signed Agent Cards and admits callers only with a
valid Keycloak token and an OPA decision. Our agents call remote agents as ordinary tools, but
only agents on an allowlist whose cards are signed by keys we pinned. Replies stay untrusted
data. Tests prove both directions with Pydantic AI and the refusals.
