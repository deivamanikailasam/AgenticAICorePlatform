# Promotion to production

This guide grows with Steps 37–40. It describes how Project A moves from the Mac to local
Kubernetes, then staging and production, and finally to a cloud provider. Until Step 38 lands,
only the principles below apply.

## Principles fixed in Step 1

- **Same scripts everywhere.** The provisioning scripts in `scripts/provision/` are idempotent
  so they can run as Kubernetes Jobs in every environment.
- **No shared credentials across environments.** Each environment gets its own database,
  Keycloak client, OpenBao paths and LiteLLM keys.
- **Everything through Git.** Promotion is a pull request; Argo CD applies what is merged.
- **Pinned everything.** Images by digest, actions by SHA, Python packages by `uv.lock`.

## Authorisation (Step 10)

- **Policies as signed bundles.** OPA pulls `policies/agent/**` (rules and `config/data.json`)
  as a signed bundle (Step 38); the dev-only PUT loader is not used outside local and CI.
- **Kill switch is data.** An incident runbook changes `kill_switch` in the data file through a
  reviewed pull request, or, in an emergency, through the bundle server; both are audited.
- **Cerbos (if chosen):** run it with the policy directory from the same release, more than one
  replica, and the parity test in the release pipeline.
- **OpenFGA (if chosen):** use the Postgres datastore (`OPENFGA_DATASTORE_ENGINE=postgres`, run
  `openfga migrate`), a pre-shared key or OIDC from OpenBao, and make the owning applications
  write relationships. The memory datastore is for dev and CI only.
- **Rate limits** share Valkey with idempotency keys; size Valkey for one counter per tenant,
  agent and tool per minute.
- **Token exchange audiences.** Every downstream audience (rag-core, each on-behalf-of MCP
  server) is listed in `infra/provision/keycloak.yaml` and provisioned per environment.

The full walkthrough (local Kubernetes, environments, release flow, agent versioning, scaling
and HA, hardening, operations, cloud move) is written in Step 38.
