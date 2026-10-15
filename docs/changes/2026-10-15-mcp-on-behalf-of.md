# External MCP tools act with the user's rights, not the platform's

**Step:** 10 · **Commit:** feat(agent-tools): call MCP servers on behalf of the user via token exchange · **Date:** 2026-10-15

## What changed
`platform.yaml` MCP servers can now use `auth.kind: on_behalf_of` with an `audience`. For each
call, the platform takes the verified token of the person the call is for, exchanges it at
Keycloak (OAuth token exchange, RFC 8693) for a new token that is valid only for that audience,
and opens a short-lived connection with it. If there is no user token the call is refused; it is
never sent with the platform's own service account. Listing the server's tools uses the
platform's client credentials (listing is not acting for anyone). Audit events record
`on_behalf_of: true`. The gateway passes its token exchanger to the MCP pool.

## Why
Like a pharmacist who fills a prescription only in the patient's name: the platform can carry
your request to the CRM, but the CRM sees *you* and gives you exactly what you are allowed to
see, not what a master key would open.

## How to check it
`uv run pytest libs/agent-tools/tests/test_mcp.py -k on_behalf` (refused without a user token,
audience required). With the stand-in up, `just test-integration` runs
`test_on_behalf_of_server_sees_the_user_and_never_the_platform`: a server that accepts only
`aud=rag-core` tokens reports `user: alice` when called as alice, and the anonymous call fails.

## Risks and follow-ups
One connection per call costs a TLS and MCP handshake each time; acceptable for now, pool by
user later if latency matters. Each audience must be listed in `infra/provision/keycloak.yaml`
so provisioning creates its client scope.
