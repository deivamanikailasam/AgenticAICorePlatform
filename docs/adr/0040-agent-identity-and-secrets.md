# ADR 0040: Per-agent identities, delegated exchange, and call-time credentials from OpenBao

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 29
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 29 asks for:
- each agent to have a Keycloak service identity;
- user requests to carry delegated, down-scoped tokens (OAuth token exchange);
- tools to get short-lived credentials from OpenBao or Vault at call time, never from the
  prompt or the environment;
- credentials never to enter model context;
- optional workload identity (SPIFFE/SPIRE) in Kubernetes;
- an alternative: cloud IAM and secret managers.

Done when:
- a tool call's audit record names the user, the agent and the credential lease;
- a leaked lease expires within minutes;
- no secret appears in any trace or prompt.

Before this step:
- the platform acted with one Keycloak client (`agent-platform`) and a single exchange hop
  (Step 10);
- every `${secret:...}` reference was read from environment variables;
- LiteLLM keys sat in a file waiting to be moved by hand (open issue 4).

## Decision
1. **OpenBao, two roles.**
   - The KV v2 mount `agent-platform` holds the platform's static secrets: Keycloak client
     secrets and LiteLLM keys.
   - A database secrets engine (`agent-db`) issues tool credentials. Each call gets a new
     Postgres user that lives 5 minutes (15 at most) and belongs to a read-only group role.
   - Revoking a lease ends the user's open sessions and drops the user. An unrevoked lease
     dies on its own.
   - The broker refuses any lease longer than 15 minutes (`MAX_TTL_SECONDS`, part of the
     contract).
2. **A `credential_broker` contract.** `issue`, `revoke` and `valid`. `Credential` keeps its
   values in `SecretStr`, left out of every dump; `lease()` is what audits keep. OpenBao and
   Vault (same HTTP API) are implemented; AWS, GCP and Azure are documented stubs.
3. **Credentials only at call time, only through the authorising executor.**
   - A spec grants a source per tool (`tools[].credentials: agent-db/creds/tool-reader`). The
     spec schema accepts only `<mount>/creds/<role>`, never a DSN or a value.
   - After policy and the rate limit, the executor issues the credential and hands it to the
     tool through a context variable. It revokes the credential when the call ends and audits
     the lease next to the user and agent.
   - A result that contains the credential is withheld.
   - Missing or refused credentials stop the call (fail closed).
4. **One Keycloak client per agent.**
   - `agent-<name>` comes from the specs in `agents/`. Its service-account tokens carry
     `aud=openbao`, so the agent logs in to OpenBao as itself (JWT auth, role
     `agent-<name>`, bound to `azp`).
   - Its OpenBao policy may issue and revoke only the sources its spec grants, and has no
     default policy.
   - A spec author can't widen an agent's access by editing the spec alone. Provisioning,
     which applies the spec to OpenBao, has to run as well, and in staging and prod the spec
     version also needs approval (Step 25).
5. **Two-hop delegation.**
   - The platform exchanges the user's token for one addressed to the agent
     (`agent-platform:agent-<name>` scope, which carries the user's groups).
   - The agent exchanges that for the downstream service, and Keycloak only lets it name the
     audiences its spec's tools need.
   - The final token names the user (`sub`, groups) and the agent (`azp`). A user token can't
     skip the platform.
   - The MCP gateway, where a person calls tools directly, keeps the single hop.
6. **Secrets come from OpenBao.**
   - Services resolve secrets with the runtime's AppRole, whose policy is read-only on KV.
     Environment variables are a fallback only in dev and CI.
   - In staging and prod, a process without OpenBao refuses to start.
   - LiteLLM role keys are loaded from OpenBao at start-up. Provisioning imports them and
     deletes the hand-off file (open issue 4).
7. **Workload identity and cloud managers are stubs.** In Kubernetes, a SPIFFE JWT-SVID
   replaces the AppRole secret id through the same JWT auth. The cloud brokers map to AWS STS
   or RDS IAM auth, GCP service-account tokens, and Azure managed identity. None can be built
   or verified without a cluster or cloud account; Step 37 finishes them.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Static tool secrets in KV, read per call | They don't expire, so a leaked one lasts until someone rotates it. Use only for systems that can't issue short-lived credentials, with rotation |
| Credentials passed as tool arguments | The model would see them (prompt, transcript, traces) |
| One platform identity for all agents | One compromised or misconfigured agent would get every agent's access. Audits couldn't tell agents apart downstream |
| Keycloak token exchange straight from the user's token to the agent | Keycloak refuses it ("not within the token audience") unless every user token is addressed to every agent. The platform hop keeps the run in control |
| AppRole per agent | Another secret per agent to distribute. The Keycloak identity already exists, and JWT auth reuses it |
| Vault instead of OpenBao | Same API (`credential_broker.vault`). Choose it where Vault Enterprise is the standard |
| Cloud secret managers and IAM | The production alternative on that cloud (stubs, ADR'd here); see Context |

## Consequences
- Tool calls that need credentials cost an OpenBao round trip to issue and one to revoke.
  Each also adds a short-lived database login.
- Keycloak issuer: containers and the host reach Keycloak under different names, so OpenBao's
  `bound_issuer` is set only when `AGENT_BAO_JWT_ISSUER` is given. Tokens are still verified
  against the realm's signing keys, `aud` and `azp`. VERIFY-ON-MAC: set it once Project 0's
  Keycloak has a fixed hostname.
- Outside Kubernetes, the AppRole secret id is the runtime's bootstrap credential.
- The run API emits spans from Step 30. Until then the "no secret in traces" check runs on the
  traced runner.
- Clients for agents removed from `agents/` aren't deleted automatically.
