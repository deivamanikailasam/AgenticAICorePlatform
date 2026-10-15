# Proof: asking for another tenant's data is refused and written down

**Step:** 10 · **Commit:** test(mcp-gateway): deny and audit a search for another tenant's documents · **Date:** 2026-10-15

## What changed
`rag-core__search` takes an optional `tenant_id` ("whose documents"). Policy denies any tenant
but the caller's; rag-core itself still takes the tenant from the exchanged token, so the value
is never sent on. A new end-to-end test signs in as alice (tenant acme) through Keycloak, calls
the live gateway with `tenant_id: globex`, and checks: the call fails with `cross-tenant access`,
exactly one `policy.decision` audit event records alice, acme, `deny` and the reason, and only the
same-tenant call that follows is executed.

## Why
This is Step 10's "Done when". Like testing a hotel key card on someone else's door: the door
must stay shut, and the front desk's log must show who tried.

## How to check it
With the stand-in up: `uv run pytest -m integration services/mcp-gateway/tests/test_gateway_integration.py -k another_tenant`
prints `PASSED`.

## Risks and follow-ups
The check depends on tools naming the tenant they act on; tools that hide it inside free text
are covered by rag-core's own token-based filtering, not by this rule.
