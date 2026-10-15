# Act with the user's own rights: token exchange towards downstream services

**Step:** 08 · **Commit:** feat(identity): add RFC 8693 token exchange and provision it in Keycloak · **Date:** 2026-10-15

## What changed
- `TokenExchanger` swaps a user's token for a new one that is valid **only** for one downstream
  service (e.g. `rag-core`) and still says who the user is (subject and groups). Results are
  cached until shortly before they expire. Refusals become clear permission errors.
- `subject_token` is a per-request context variable holding the verified user token while one
  call runs; it is never stored in run state, checkpoints, prompts or traces.
- Keycloak provisioning now enables standard token exchange on the `agent-platform` client and,
  for each audience in `infra/provision/keycloak.yaml`, creates an optional client scope
  `agent-platform:<audience>` with an audience mapper (and group claims), attached to the client.
- The Keycloak token verifier passes group and role claims through.
- The stand-in realm gains test users in two tenants (alice and bob in `acme`, gina in `globex`),
  a test-only public client to obtain user tokens, and a `rag-core` resource-server client.

## Why
A tool that searches documents "for Alice" must see only what Alice may see. Forwarding Alice's
own token would let the downstream service (or anyone who intercepts it) use it anywhere; using
the platform's powerful service account would ignore her permissions. Token exchange gives a
one-purpose pass, like a hotel key card that opens one room for one night.

## How to check it
`uv run pytest libs/agent-tools/tests/test_identity.py` (unit). Against the stand-in, the gateway
integration test in the next commit exchanges real Keycloak tokens.

## Risks and follow-ups
Requires Keycloak 26.2+ standard token exchange (26.8.1 in the stand-in). Project 0's realm must
have the downstream clients (`rag-core`) and group claims configured (VERIFY-ON-MAC). Per-agent
identities and OpenBao-held credentials arrive in Step 29.
