# Promotion to production

How Project A moves from the Mac to local Kubernetes, then staging and production, and on to a
cloud provider. Commands are in the `justfile`; decisions are in ADR 0049 (Step 38) and the ADRs it
names. Items marked VERIFY-ON-MAC depend on Project 0's own setup and are checked on the Mac.

## Principles fixed in Step 1

- **Same scripts everywhere.** The provisioning scripts in `scripts/provision/` are idempotent
  so they can run as Kubernetes Jobs in every environment.
- **No shared credentials across environments.** Each environment gets its own database,
  Keycloak client, OpenBao paths and LiteLLM keys.
- **Everything through Git.** Promotion is a pull request; Argo CD applies what is merged.
- **Pinned everything.** Images by digest, actions by SHA, Python packages by `uv.lock`.

## 1. Local Kubernetes first

On the Mac, OrbStack's Kubernetes runs Project 0 in the `rag-platform` namespace (VERIFY-ON-MAC:
namespace and service names; `deploy/overlays/*/env.properties` assumes them). Models stay native
and are reached through LiteLLM.

1. **Cluster add-ons.** `just cluster-addons`, then:
   - `kustomize build deploy/argocd/install | kubectl apply --server-side -f -`
     (Argo CD v3.5.3, images by digest);
   - `kustomize build deploy/keda | kubectl apply --server-side -f -` (KEDA 2.21.0);
   - External Secrets Operator, for Temporal's database password in staging and prod
     (VERIFY-ON-MAC).
2. **OpenBao per environment.** For each environment:
   - a KV mount `agent-platform-<env>` holding the secrets the services reference:
     `agent-platform/db#dsn`, `agent-platform/audit#dsn`, `agent-platform/db-migrate#dsn`,
     `agent-platform/checkpoints#key`, `agent-platform/a2a#signing_key`,
     `keycloak/<client>#client_secret`, `litellm/<role>#api_key`;
   - Kubernetes auth with a role `agent-platform-<env>` bound to that namespace's service accounts
     (agent-api, agent-worker, mcp-gateway, a2a-gateway, agent-db-migrate), audience `openbao`.
   `scripts/kind_e2e.py` shows the exact calls, against the stand-in.
3. **Argo CD apps.** `kustomize build deploy/argocd | kubectl apply -f -`: the project, one
   Application per environment, and the Temporal cluster for staging and prod.
4. **Check.** `just deploy-check` before every change. CI's `deploy-e2e` runs the same on kind:
   admission of every overlay, dev synced by Argo CD, a run through agent-api.

## 2. Environments

| | dev | staging | prod |
|---|---|---|---|
| Follows | `agents/` at main's head | `deploy/releases/staging.yaml` | `deploy/releases/prod.yaml` |
| Changes by | merging to main | a promotion pull request | a promotion pull request with the named approver |
| Replicas (API, MCP, A2A) | 1 | 2 | 3 |
| Workers | 1 | KEDA 1–4 on queue backlog | KEDA 2–10 on queue backlog |
| Temporal | dev server in the namespace | cluster in `temporal-staging` | cluster in `temporal-prod` |
| Namespace, OpenBao mount and role, Keycloak client | `agent-platform-dev` | `agent-platform-staging` | `agent-platform-prod` |

Every namespace enforces Pod Security `restricted` and denies traffic by default. Nothing is
exposed outside the cluster except through its ingress, which is labelled
`agent-platform/ingress=true`.

## 3. Release flow

1. **Pull request.** CI runs:
   - lint, types, unit, contract, replay and Rego tests;
   - the deployment check and the promotion rules;
   - the eval gates for the agents touched;
   - the red team.
   The `images` workflow builds, scans, signs and publishes each image by digest.
2. **Merge.** Argo CD syncs dev.
3. **Platform code.** `just deploy-images <env> <commit>` in a pull request moves an environment
   to a commit's signed images.
4. **An agent version.**
   1. `just promote <agent> --to staging --commit`. The pull request snapshots the version from
      dev and makes staging serve it. The full eval and red-team suites run (nightly and release
      workflows).
   2. `just promote <agent> --to prod --commit`. Prod gets a 10% canary (`--shadow` for a shadow
      run). The named approver reviews it.
   3. Watch `GET /v1/admin/releases`. The online evaluation scores every canary run against the
      stable version: failures in percentage points, cost and time growth in percent, after a
      minimum number of runs.
   4. **If it breaches,** the canary is rolled back at once on every replica. `AgentReleaseRolledBack`
      fires (runbook `release-rollback.md`). Open `just promote <agent> --abort` so Git matches,
      fix the agent, and release a new version.
   5. **If it passes,** `just promote <agent> --to prod --full --api <prod run API> --commit`
      refuses unless the live verdict is `pass`. Its pull request makes the canary prod's stable
      version.
5. **Rollback of a whole environment.** Revert the merge. Argo CD syncs back. Images and specs
   are immutable, so a revert restores exactly what ran before.

## 4. Agent versioning

- Specs, prompts, tools and models are versioned separately. Every run and audit event records
  all four (Step 31). A run finishes on the spec version it started with, also after a restart or
  a takeover by another replica.
- Released versions live in `deploy/specs/<agent>/<version>/` and never change.
- Durable (Temporal) agents are released whole: no canary or shadow until Temporal worker
  versioning keeps in-flight workflows on their version. The releases file refuses a candidate
  for them.

## 5. Scaling and HA

- **API:** stateless behind the Service. Replicas share runs through leases in the agent database.
  A lost replica's runs are taken over within a lease period (30 s) and continue from their
  checkpoints. Retention runs on one replica at a time.
- **Workers:** KEDA scales on the `agent-platform` task queue's backlog. Activities heartbeat, and
  a stopping worker is given 120 s.
- **MCP and A2A gateways:** replicated with client affinity, because sessions and tasks live in
  memory.
- **Temporal:** the cluster chart with 2 or 3 replicas of each service and Postgres persistence;
  or Temporal Cloud.
- **Postgres:** Project 0's, with high availability and point-in-time recovery; **Valkey** with
  replicas (VERIFY-ON-MAC).

## 6. Security hardening

- Pod Security `restricted`. No Kubernetes API token in any pod. A 10-minute OpenBao token only.
- Network policies deny by default. Each service may receive only from its callers and send only
  to DNS, Project 0's service ports, Temporal and the services it calls. External tools and agents
  go through an egress proxy with a per-host allowlist (to set up per cluster).
- Images by digest, signed by the `images` workflow. Next: verify signatures at admission
  (Sigstore policy controller or Kyverno).
- mTLS between pods via a service mesh. Workload identity (SPIFFE, ADR 0040 stub). The sandbox on
  a dedicated node pool with gVisor (`runtimeClassName`).
- No Secret objects in Git. Temporal's password is synced from OpenBao by External Secrets.

### Authorisation (Step 10)

- **Policies as signed bundles.** OPA pulls `policies/agent/**` (rules and `config/data.json`)
  as a signed bundle; the dev-only PUT loader is not used outside local and CI.
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

## 7. Operations

- **SLOs and alerts:** `infra/observability/prometheus/agent-rules.yaml`, with a runbook for every
  alert in `docs/runbooks/`.
- **DR:** `governance/dr.yaml` states RPO 300 s and RTO 1800 s for the agent database.
  - `just dr-drill` rehearses it: weekly in CI (`dr-drill` workflow), quarterly on the real
    environment.
  - Restore with `docs/runbooks/dr-restore.md`.
- **Kill-switch drills** (Step 35 red team), quarterly access reviews of agent permissions
  (registry and OpenBao policies), cost reviews per tenant and per agent (`just cost-reconcile`).

## 8. Cloud move

Use the cloud mapping in the build guide (Phase I). Replace local Qwen with a managed or
GPU-hosted model behind the same LiteLLM aliases, so specs don't change. EKS, AKS or GKE take the
same overlays. Swap these for the provider's:
- Project 0's endpoints in `env.properties`;
- the OpenBao address, or a cloud secret manager through the ADR 0040 brokers;
- the WAL archive bucket.
