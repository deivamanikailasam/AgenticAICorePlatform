# One rule decides which tenant a user belongs to

**Step:** 17 · **Commit:** refactor(agent-tools): share the tenant rule between services · **Date:** 2026-10-15

## What changed
`tenant_of` (the tenant is the top level of the user's Keycloak group paths, and users in
several tenants are refused) moved from the MCP gateway into `agent_tools.auth`. The gateway
imports it from there; the run API uses the same function.

## Why
Tenant isolation must be decided identically at every entry point; two copies could drift.

## How to check it
`uv run pytest services/mcp-gateway libs/agent-tools` — unchanged behaviour.

## Risks and follow-ups
VERIFY-ON-MAC (unchanged): align with Project 0's tenant claim convention if it differs.
