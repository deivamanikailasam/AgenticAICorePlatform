# One checkpoint in front of every tool: policy, rate limit, audit

**Step:** 10 · **Commit:** feat(agent-tools): add the authorising executor with Valkey rate limits · **Date:** 2026-10-15

## What changed
- New `AuthorisingToolExecutor` (`tool_executor.authorising`) wraps the platform executor. For
  every call it looks the tool up, asks the policy decision point (with the caller, tenant, agent,
  tool ref and tags, side effect, data class, arguments, risk tier and the agent's granted tools),
  counts the call against a per-minute limit, runs it, and writes an audit event for the decision
  and the outcome, allowed or not.
- A grant in an agent's spec can turn "allow" into "needs approval", never the other way round.
- Tools that address tenant-owned data take a `tenant_id` argument; it becomes the policy's
  `resource_tenant_id`, so a call for another tenant's data is denied and audited.
- Rate limits: a one-minute window per tenant, agent and tool. `ValkeyRateLimiter` shares the
  count across replicas in Project 0's Valkey; `MemoryRateLimiter` for tests.
- The MCP gateway now uses this executor instead of its own inline policy and audit code. People
  calling through it get a default limit (`MCP_GATEWAY_RATE_LIMIT_PER_MINUTE`, 120).

## Why
Like the single security desk in a building lobby: whoever you are and wherever you are going,
you pass the same desk, it checks your badge, counts how often you come through, and writes it
in the log book. Having one desk instead of one per floor means no floor forgets to check.

## How to check it
`uv run pytest libs/agent-tools/tests/test_authorising.py` covers allow, deny (cross-tenant),
approval from the spec, rate limiting and the executor contract. With the stand-in up,
`just test-integration` includes the shared Valkey count across two limiter instances and the
gateway's live calls.

## Risks and follow-ups
The fixed window allows a burst of up to twice the limit across a minute boundary; acceptable for
abuse protection, not for billing. The `tenant_id` argument convention must be followed by tool
authors (documented in how-to/add-a-tool).
