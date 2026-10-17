# The platform's endpoints, for other teams

Three services face callers outside the platform. Each needs a Keycloak bearer token from Project
0's realm, with audience `agent-platform`. Each checks every call with policy (OPA) and writes it
to the audit trail. Inside a cluster, they are Services in the environment's namespace
(`agent-platform-<env>`, or a project's own). They are never exposed except through the
cluster's ingress. In local development they listen on 127.0.0.1.

| Service | Port | Use it for |
|---|---|---|
| Run API (`agent-api`) | 8091 | Running the platform's agents from an application: create a run, stream its events, approve |
| MCP gateway (`mcp-gateway`) | 8090 | Calling the platform's tools from any MCP client (an IDE, another agent framework) |
| A2A gateway (`a2a-gateway`) | 8092 (HTTP), 8093 (gRPC, one agent) | Calling the platform's published agents from another agent (A2A v1.x) |

## Run API

Source: `services/agent-api/src/agent_api/app.py`. Every route needs `Authorization: Bearer
<token>`. A WebSocket sends the token in its first message, so tokens never appear in URLs.

| Method and path | What it does | Who may |
|---|---|---|
| `GET /healthz` | Liveness | Anyone |
| `GET /v1/agents` | The agents this API can run, with their versions | Any caller |
| `POST /v1/agents/{agent}/runs` | Starts a run. Body `{"input": {...}, "thread_id": "optional"}`. Returns 201 with `run_id` and `status`. `Idempotency-Key` is honoured | Any caller the agent's policy admits. In staging and prod, only approved agent versions run |
| `GET /v1/runs/{id}` | State and pending approvals | The run's owner |
| `GET /v1/runs/{id}/events?after=N` | AG-UI events over SSE. The stream ends when the run is idle | The run's owner |
| `WS /v1/runs/{id}/ws` | The same events over a WebSocket | The run's owner |
| `POST /v1/runs/{id}/approvals` | Approve or reject a pending call: `{"decision": "approve"}` or `"reject"`, with `arguments` when editing | The approver group the approval desk routed it to. Never the requester |
| `GET /v1/approvals` | The caller's approval inbox | Approvers |
| `GET /v1/reviews`, `POST /v1/reviews/{id}` | Review-after sampling | Reviewers |
| `POST /v1/runs/{id}/resume`, `/cancel`, `/retry`, `/takeover` | Answer a non-approval interrupt, cancel, continue from checkpoints, operator take-over | Owner (take-over: operators) |
| `GET /v1/threads`, `GET /v1/threads/{id}/checkpoints`, `POST /v1/threads/{id}/fork` | Threads and time travel | The thread's owner |
| `GET /v1/me/export` | The caller's own runs, events and memories | Any caller |
| `GET /v1/memories?agent=A`, `DELETE /v1/memories/{agent}/{id}` | Inspect and delete one's own memories | Any caller |
| `POST /v1/admin/erasure`, `/v1/admin/export`, `/v1/admin/retention`, `/v1/admin/memory/flush` | Privacy operations | Privacy officers |
| `GET /v1/admin/releases` | Stable versions, candidates, flags and online-eval verdicts | Operators |

From an agent project: `agentctl run <agent> --api https://<run API> --input '{...}'`, with the
token in `AGENT_API_TOKEN`.

## MCP gateway

Source: `services/mcp-gateway/src/mcp_gateway/app.py`. The MCP streamable HTTP transport is at
`/mcp`. OAuth protected-resource metadata is under `/.well-known/` (public) and `GET /healthz`
is public.

- **Tools:**
  - `platform__<name>`: the platform's own tools (calculator, clock, word count).
  - `rag-core__search`: Project 0's retrieval, called on the user's behalf by token exchange.
  - `<server>__<tool>`: tools of the allowlisted MCP servers it proxies (`MCP_GATEWAY_PROXY`).
- **On every call:** the token is verified, then policy decides (OPA by default, or Cerbos or
  OpenFGA by `MCP_GATEWAY_PDP`). The call is rate limited per tenant, agent and tool (120 a minute by default)
  and audited. Irreversible tools need an approval, as in runs.
- **Hosts:** only those in `MCP_GATEWAY_ALLOWED_HOSTS` (DNS-rebinding protection).

A client connects like this (Python MCP SDK):

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async with streamable_http_client(
    "https://<gateway>/mcp", headers={"Authorization": f"Bearer {token}"}
) as (r, w, _):
    async with ClientSession(r, w) as session:
        await session.initialize()
        print(await session.list_tools())
```

## A2A gateway

Source: `services/a2a-gateway/src/a2a_gateway/app.py`. It publishes the agents whose spec sets
`interop.a2a_publish: true`.

| Method and path | What it does | Auth |
|---|---|---|
| `GET /` | Index of published agents and their card URLs | Public |
| `GET /healthz` | Liveness | Public |
| `GET /agents/<name>/.well-known/agent-card.json` | The agent's Agent Card, signed with ES256 by the platform's key (`a2a.publish.key_id`) | Public |
| `POST /agents/<name>/jsonrpc` | The A2A JSON-RPC binding, with the methods of the a2a-sdk version pinned in `uv.lock` | Token (401), then OPA `a2a.inbound` (403) |
| `/agents/<name>/rest/...` | A2A HTTP+JSON binding | Same |
| gRPC on 8093 | The gRPC binding, for the one agent `A2A_GATEWAY_GRPC_AGENT` names | Same |

- **Running:** a task runs through the run API as the caller. Its policies, approvals and audit
  apply, and the tenant comes from the token, never from the URL.
- **Push notifications:** only to hosts in `a2a.publish.push_allowed_hosts`, over HTTPS.
- **Verify the signature:** callers should check the card against the platform's published key
  before sending a message. The platform does the same when it calls other agents (`a2a.agents`
  allowlist with pinned keys).

## Calling other teams' agents from the platform

- **A2A:** a spec grants another team's agent as a tool, `a2a://<name>`. It must be on
  `a2a.agents` in `platform.yaml`, with its card keys pinned.
- **MCP:** a spec grants another team's MCP server's tools as `mcp://<server>/<tool>`. The
  server must be on the allowlist, and its tool descriptions are screened for injections.
- **SDK:** a team's own code ships as a package of tools, functions, patterns and evaluators,
  installed into its images (`agent-sdk`, Step 39).
