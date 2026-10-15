# Per-agent identities, delegated exchange, secrets from OpenBao

**Step:** 29 · **Commit:** feat(agent-api): per-agent identities, delegated token exchange and secrets from OpenBao · **Date:** 2026-10-15

## What changed
- **Agent identities.**
  - `just provision-keycloak` gives every spec in `agents/` its own Keycloak client, `agent-<name>`:
    - Its service-account tokens carry `aud=openbao`.
    - A scope, `agent-platform:agent-<name>`, lets the platform delegate a user's request to the agent.
    - The agent may exchange only for the downstream audiences its tools call for the user. These are read from the spec by `scripts/provision/agents.py`.
  - `just provision-openbao` then:
    - copies every client secret into KV (`keycloak/<client>`);
    - enables JWT auth on the realm's signing keys;
    - gives each agent a role (`aud=openbao`, `azp=agent-<name>`, no default policy) and a policy that may issue and revoke only the credential sources its spec grants.
- **Delegation.**
  - `DelegatingExchanger` exchanges in two down-scoping hops: user → platform → `agent-<name>` → service.
  - `acting_agent` is set by the authorising executor for each agent's tool call. The MCP gateway, where no agent is acting, keeps the single hop.
  - `AgentIdentities` provides each agent's service token, its exchanger, and its OpenBao JWT login (`bao_auth`).
- **Credentials.** The services' `OpenBaoCredentialBroker` logs in as each agent, so OpenBao's own policy bounds what each agent can obtain.
- **Secrets.** `default_resolver()` replaces `EnvSecretResolver()` in the services and libraries:
  - With the runtime's AppRole configured, secrets come from OpenBao, with env as a fallback only in dev and CI.
  - In staging and prod without OpenBao, the process refuses to start.
  - The LiteLLM role keys are loaded from `litellm/<role>` at start-up (`load_gateway_keys`). Outside dev, they never come from env.
- **Wiring.**
  - `agent_api.identity.identity_from_env` connects all of this for agent-api and agent-worker. With OpenBao it uses per-agent identities; `AGENT_IDENTITY_MODE` overrides the choice.
  - Compose passes `AGENT_BAO_ROLE_ID` and `AGENT_BAO_SECRET_ID` through.

## Why
Step 29: "Each agent has a Keycloak service identity; user requests carry delegated, down-scoped tokens (OAuth token exchange); tools get short-lived credentials from OpenBao/Vault at call time". The security defaults say secrets come only from OpenBao.

## How to check it
- `uv run pytest libs/agent-tools/tests/test_agent_identity.py services/agent-api/tests/test_gateway_keys.py tests/scripts/test_provision.py`. These check:
  - the hop order, clients, scopes and subject tokens;
  - that the executor names the acting agent;
  - that the resolver refuses env in staging and prod;
  - that gateway keys come only from OpenBao in prod;
  - that agent needs and policies follow the specs.
- `just test-integration services/agent-api/tests/test_agent_identity_live.py`. Against live Keycloak 26.8.1 and OpenBao 2.7.0, with a throwaway agent:
  - Its token names it, and OpenBao accepts it as that agent.
  - It obtains its granted 5-minute credential and can revoke it. An agent granted nothing is refused.
  - Its OpenBao token can't read the platform's secrets.
  - Alice's request reaches rag-core as `aud=rag-core`, `azp=agent-identity-probe`, with her groups.
  - An agent without rag-core in its spec is refused, and a user token sent straight to an agent is refused.
- A second run of `just provision-keycloak` and `just provision-openbao` changes nothing.

## Risks and follow-ups
- **Issuer check.** `bound_issuer` is set only when `AGENT_BAO_JWT_ISSUER` is given. Containers reach Keycloak as `keycloak:8080` while the host uses `127.0.0.1:8080`, so the `iss` claim differs. Tokens are still checked against the realm's own signing keys, `aud=openbao` and `azp`. VERIFY-ON-MAC: set it once Project 0's Keycloak has a fixed hostname.
- **Pod identity.** Outside Kubernetes, the runtime's AppRole secret id is the bootstrap credential. In Kubernetes, workload identity replaces it (the SPIFFE/SPIRE stub follows).
- **Removed agents.** Clients for agents removed from `agents/` are not deleted automatically.
