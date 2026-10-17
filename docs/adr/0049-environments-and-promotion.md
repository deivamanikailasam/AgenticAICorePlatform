# ADR 0049: Environments and promotion: Kustomize and Argo CD, signed images by digest, shared runs across replicas, agent versions released by pull request through a 10% canary with an online-eval gate, and measured disaster recovery

- **Status:** Accepted
- **Date:** 2026-10-17
- **Step:** 38
- **Deciders:** repository owner (delegated the open decisions, 2026-10-16)

## Context
Step 38 asks for:
- Kustomize overlays for dev, staging and prod, and Argo CD apps;
- promotion by Git pull request with eval gates and approvals;
- versioned specs deployed as config;
- canary and shadow runs for new agent versions, online evaluation on sampled production runs,
  and feature flags per agent;
- worker autoscaling on queue depth, and HA for the API, workers, Temporal and the MCP gateway;
- backups and DR drills for checkpoints, memory and audit.

Done when:
- a new agent version goes dev → staging → 10% canary → prod by PR only;
- a failed online-eval gate rolls back automatically;
- a DR drill restores state within the stated RPO/RTO.

Constraints found while building it:
- **The run API was not stateless.** A run lived in the memory of the process that created it.
- **No images were published.**
- **Registries were partly blocked.** This build container can't pull from quay.io or GitHub's
  container blob storage, so Argo CD and KEDA can't run here.
- **kind can't run here.** Docker is nested and runc may not set `oom_score_adj`.
- **Prerequisites live outside this repo.** Project 0's Kubernetes namespace and its Postgres
  backups belong to Project 0 (VERIFY-ON-MAC).

## Decision
1. **Kustomize base plus overlays, synced by Argo CD.**
   - The base carries the compose hardening into Kubernetes: non-root, read-only root filesystem,
     no privilege escalation, all capabilities dropped, seccomp, memory limits, probes, disruption
     budgets, spread, deny-by-default network policies, and a pre-sync migration Job with its own
     DDL credential.
   - Overlays set each environment's namespace (Pod Security `restricted` enforced), config,
     OpenBao role and mount, Keycloak client and image digests.
   - dev runs one replica of everything and a Temporal dev server. Staging runs 2 replicas, prod 3.
     In both, workers scale on the Temporal task-queue backlog with KEDA, and a Temporal cluster
     comes from its Helm chart at a pinned commit.
   - Argo CD has one AppProject (this repo and Temporal's chart only; Namespaces as the only
     cluster-scoped kind) and an Application per environment, self-healing.
   - `just deploy-check` renders all three, validates them against Kubernetes 1.37 schemas pinned
     to a commit, and applies `deploy_policy`, whose rules are the spec's security defaults.
2. **Secrets only from OpenBao, without static credentials in the cluster.**
   - Pods log in with a projected service-account token for audience `openbao` (10 minutes, no
     Kubernetes API token mounted).
   - Database connection strings are `${secret:...}` references resolved at start-up.
   - The only Kubernetes Secret is Temporal's database password, synced from OpenBao by External
     Secrets.
3. **Images built once, scanned, signed, by digest.**
   - The `images` workflow builds the five images per commit with an SBOM and provenance, fails on
     fixable critical vulnerabilities (Trivy), and signs keylessly (cosign) with its own
     identity, verified.
   - `just deploy-images` pins an environment to a commit's digests.
   - Third-party images are pinned in `deploy/third-party-images.txt` and checked against their
     registries.
4. **Shared runs across API replicas: leases.**
   - Each unfinished run is driven by the replica holding its lease in the run store.
   - Other replicas forward run-scoped calls to it and read its events from the store.
   - Lapsed leases are taken over and the run continues from its checkpoints. A replica that
     lost a lease lets go without writing.
   - Lists come from the store, and idempotent creation works across replicas.
   - MCP and A2A gateways keep a client on one replica (session affinity), because their
     sessions are in memory.
5. **Agent versions are configuration; canaries are agent-level.**
   - `deploy/releases/<env>.yaml` names each agent's stable version and, in prod, a candidate:
     canary (a share of new threads, a thread keeps its version) or shadow (the same input,
     no side effects, nothing shown).
   - Every candidate run and a risk-tier sample of stable runs is scored online (success, cost,
     time) against a gate.
   - A breach rolls the candidate back at once on every replica (shared state), audited, counted
     and alerted. A rolled-back version never serves again.
   - An unapproved candidate never serves.
   - Feature flags switch an agent or a tool off.
   - This is chosen over an image-level canary (Argo Rollouts) because the thing being tried is
     an agent version, which shares an image with its stable version.
6. **Promotion by pull request, with the order enforced.**
   - A released version is an unchangeable snapshot (`deploy/specs/<agent>/<version>/`) taken
     from dev.
   - The promotion check refuses, in CI, any change that skips staging, starts a prod canary
     above 10%, makes a version stable in prod other than by replacing the candidate it was, or
     edits a snapshot.
   - `just promote --full` also requires the live online evaluation to say `pass`.
   - Prod's releases, snapshots, overlay and the promotion policy are governance paths (named
     approver).
7. **Point-in-time recovery, rehearsed and measured.**
   - `governance/dr.yaml` states the agent database's targets: RPO 300 s, RTO 1800 s (owner
     policy).
   - The backup is a base backup plus continuous WAL archiving off the host.
   - `just dr-drill` destroys a database under live writes from the platform's own stores,
     restores it, verifies the audit chain and every recovered item, and measures both numbers.
     It runs weekly in CI.
   - Retention runs daily inside agent-api, on one replica at a time (advisory lock).
8. **A real-cluster check in CI.** `deploy-e2e` runs:
   - kind with Argo CD and KEDA (pinned releases, images by digest);
   - server-side admission of every overlay;
   - the dev Application synced by Argo CD from the commit;
   - a run through agent-api;
   - the ingress policy enforced.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Helm for the platform | More moving parts for one consumer. Kustomize is in the spec's default stack; Helm is used where upstream ships it (Temporal) |
| Flux | Equivalent GitOps; the spec names Argo CD |
| Argo Rollouts for agent canaries | It canaries pods, not agent versions sharing an image. Kept as the option for canarying platform code |
| Sticky sessions instead of leases | A lost replica would stop its runs until restart, and lists would differ per replica |
| AppRole secret ids in Kubernetes Secrets | A static secret to leak and rotate; the pod's own short-lived token is stronger |
| Nightly logical dumps | Up to a day of loss; the spec's targets need continuous archiving |
| CronJob for retention | Needs its own credential and token. An in-process schedule with an advisory lock needs neither |

## Consequences
- **Good:**
  - What runs is what Git says.
  - A bad agent version reaches at most its canary share, and only until its gate has enough
    runs.
  - The API survives losing a replica.
  - Recovery is measured. In this container's drill, 32.3 s of writes were lost and recovery
    took 5.0 s: one host and a small database (`evals/results/dr/`).
- **Costs:** Argo CD, KEDA, a Temporal cluster, External Secrets and an object store for WAL
  must be run. The online gate sees success, cost and time only. Durable agents are released
  whole until Temporal worker versioning is wired.
- **Follow-ups:**
  - Project 0's namespace, OpenBao Kubernetes auth roles and Postgres backups on the Mac
    (VERIFY-ON-MAC).
  - Admission-time signature verification (Sigstore policy controller or Kyverno).
  - mTLS between pods.
  - A second approver for prod promotions.
