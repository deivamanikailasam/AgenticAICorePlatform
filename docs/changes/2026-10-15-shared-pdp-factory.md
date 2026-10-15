# One place chooses the policy engine

**Step:** 17 · **Commit:** refactor(agent-tools): share the policy-engine factory between services · **Date:** 2026-10-15

## What changed
The code that picks OPA or Cerbos (optionally with OpenFGA relationship checks) moved from the
MCP gateway into `agent_tools.pdp_factory`. The gateway now calls it; the run API (next commits)
will too.

## Why
Two services choosing their policy engine in two copies of the same code could drift apart, and
policy is the one thing that must behave identically everywhere.

## How to check it
`uv run pytest services/mcp-gateway libs/agent-tools` — the gateway's existing engine-selection
tests pass unchanged.

## Risks and follow-ups
None: behaviour is unchanged; error messages keep the `MCP_GATEWAY_PDP` prefix in the gateway.
