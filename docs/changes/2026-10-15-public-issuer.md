# Services in containers accept the tokens people actually have

**Step:** 17 · **Commit:** fix(agent-api): verify tokens against the public issuer inside containers · **Date:** 2026-10-15

## What changed
Inside the Docker network the services reach Keycloak as `keycloak:8080`, but users get their
tokens from Keycloak at `127.0.0.1:8080`, so every token said a different issuer and was refused.
The run API and the MCP gateway now check tokens against the public issuer
(`P0_KEYCLOAK_PUBLIC_URL`, default `http://127.0.0.1:8080`) and fetch signing keys and exchange
tokens over the internal address. Without a public URL set, both are the same, as before.

## Why
Found by running the new run API as a container and calling it with a real token. The gateway had
the same latent fault; its tests run it outside a container, so they never hit it.

## How to check it
`uv run pytest services/agent-api/tests/test_settings.py`, and with the stack up: get a token for
alice and call `http://127.0.0.1:8091/v1/agents` — it lists the agents instead of returning 401.

## Risks and follow-ups
VERIFY-ON-MAC: Project 0's Keycloak should pin its public hostname (`KC_HOSTNAME`) so the issuer
is the same for every caller; set `P0_KEYCLOAK_PUBLIC_URL` to it.
