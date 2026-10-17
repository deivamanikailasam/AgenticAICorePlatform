# Sequence: another agent calls a published agent over A2A (Step 24)

```mermaid
sequenceDiagram
  autonumber
  participant C as Caller agent
  participant G as a2a-gateway
  participant KC as Keycloak
  participant OPA as OPA
  participant API as agent-api
  participant AU as Audit
  C->>G: GET /agents/{name}/.well-known/agent-card.json
  G-->>C: Agent Card, signed ES256 with the platform's key
  C->>C: verify the signature against the published key
  C->>KC: client-credentials token (audience agent-platform)
  C->>G: POST /agents/{name}/jsonrpc: send a message (bearer token)
  G->>G: verify token (401 if not)
  G->>OPA: a2a.inbound (caller, agent, tenant from the token)
  OPA-->>G: allow, or deny (403; e.g. the agent's kill switch)
  G->>API: start the run as the caller
  API->>AU: run.started …
  API-->>G: events and result
  G-->>C: task status and artefacts (push notifications only to allowlisted HTTPS hosts)
```

The platform calling **another** team's agent is the mirror image. The spec grants
`a2a://<name>`, the agent must be on `a2a.agents` in `platform.yaml` with its card keys pinned,
and the card's signature is checked before any message is sent. Policy decides the call as it
decides any tool call.
