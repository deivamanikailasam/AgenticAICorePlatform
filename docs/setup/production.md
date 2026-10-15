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

The full walkthrough (local Kubernetes, environments, release flow, agent versioning, scaling
and HA, hardening, operations, cloud move) is written in Step 38.
