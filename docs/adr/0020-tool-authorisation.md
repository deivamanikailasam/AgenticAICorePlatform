# ADR 0020: Tool authorisation — one authorising executor, OPA by default, Cerbos and OpenFGA as options

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 10
- **Deciders:** repository owner

## Context
ADR 0009 fixed that a policy decision point decides every tool call. Step 10 makes the rules
complete (user, tenant, agent, tool, side-effect class, data classification, arguments, risk
tier), makes tools act with the user's own rights, adds per-tool rate limits, and implements the
catalogue's alternatives: Cerbos for the policy engine and OpenFGA for relationships.

Versions checked on 2026-10-15 (Docker Hub tags and digests): Cerbos **0.56.0** (2026-09-30,
`sha256:540643bc…bec1`), OpenFGA **v1.22.0** (2026-10-06, `sha256:9cf9a20a…769e`). OPA stays at
1.21.2 (ADR 0009).

## Decision
1. **One checkpoint.** `AuthorisingToolExecutor` wraps the platform executor: look up the tool,
   ask the PDP, apply the rate limit, run, audit the decision and outcome. The MCP gateway uses
   it, and the runtime will (Step 11+). Nothing else grants permissions.
2. **Rules** live in `policies/agent/tools/decision.rego`; the values operators change (kill
   switches, clearances, argument limits) live in `policies/agent/config/data.json`. Decisions
   are `allow`, `deny` or `require_approval` with reasons, approvers and the policy version.
3. **A spec can only tighten.** A tool's `approval` in agent.yaml turns allow into
   require-approval; it never loosens a deny. The tools an agent's spec lists are passed as
   `allowed_tools`, and policy denies anything else.
4. **Tenant of the data.** Tools that act on tenant-owned data take a `tenant_id` argument; it
   becomes `resource_tenant_id`, and policy denies any tenant but the caller's.
5. **On behalf of the user.** Downstream calls use RFC 8693 token exchange for one audience
   (rag-core since Step 8; MCP servers with `auth.kind: on_behalf_of` now). No user token means
   the call is refused; the platform's service account is never used to act for a user.
   Discovery (tools/list) of such servers uses client credentials, because listing is not acting.
6. **Rate limits** are a fixed one-minute window per tenant, agent and tool in Valkey
   (`agent:rl:*`), from `tools[].rate_limit_per_minute`, with a gateway default of 120/min.
7. **Cerbos** (`policy_decision_point.cerbos`) implements the same rules in
   `policies/cerbos/tool.yaml`, asked as two actions (`call`, `call_without_approval`) to express
   three effects. A live parity test requires the same effect and approvers as OPA on 19 cases;
   a unit test keeps its constants equal to `data.json`.
8. **OpenFGA** answers relationship questions through `policy_decision_point.composite`: primary
   PDP first, then the relation from `policies/openfga/relations.yaml` on a tenant-qualified
   object id. Missing relation, missing argument or engine failure means deny.
9. **Placement.** Cerbos and OpenFGA are not Project 0 services, so they run in Project A's
   compose under opt-in profiles, on 127.0.0.1, digest-pinned; OpenFGA requires a pre-shared key
   and refuses to start without one. The gateway selects with `MCP_GATEWAY_PDP`
   (`opa`, `cerbos`, `opa+openfga`, `cerbos+openfga`).
10. **Fail closed** everywhere: unreachable engine, error status or malformed answer → deny.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Checks inside each tool | Inconsistent, untestable, invisible to auditors (ADR 0009) |
| Cerbos as default | Fine engine, but Project 0 already runs OPA; one fewer service |
| Relationships in Rego data | Works for small, static data; breaks down for per-object sharing at scale — OpenFGA's job |
| Token-bucket / sliding-window rate limit | Smoother, more state; the fixed window is enough for abuse protection |
| Forwarding the user's token downstream | Leaks a broad token to every service; exchange narrows it to one audience |

## Consequences
Two copies of the rules (Rego and Cerbos) can drift; parity and constants tests guard it. Every
call pays one PDP round trip, plus one for OpenFGA when a relation applies. OpenFGA's memory
datastore is for dev/CI only; production needs its Postgres datastore and applications that write
relationships. The fixed rate window can let up to twice the limit through across a minute
boundary.

## Verification
`just policy-test` (Rego 21/21, Cerbos 9/9); `uv run pytest libs/agent-tools/tests/test_authorising.py`;
with the stand-in and `just up-alternatives`: the parity, OpenFGA, Valkey limiter, on-behalf-of
MCP and `test_another_tenants_data_is_denied_and_audited` integration tests.
