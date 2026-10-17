# Step 38 · Environments and promotion

## 1. At a glance

| | |
|---|---|
| **Goal** | Run the platform on Kubernetes in three environments. Move a new agent version through them only by reviewed pull requests, try it on a small share of production first, roll it back by itself if it does worse, and prove the data can be restored after a disaster |
| **Time** | About three days |
| **You end up with** | `deploy/` (Kustomize base, dev/staging/prod overlays, Argo CD apps, Temporal cluster, KEDA, add-ons pinned by digest); images built, scanned, signed and published by digest; run leases so API replicas share runs; releases with canary, shadow, flags, online evaluation and automatic rollback; `just promote` and the promotion check; `just dr-drill` with stated RPO/RTO; scheduled retention; a kind + Argo CD end-to-end check in CI |
| **New words** | Kustomize overlay, GitOps, Argo CD, Pod Security Standards, NetworkPolicy, KEDA, lease, canary, shadow run, online evaluation, rollback, feature flag, snapshot, RPO, RTO, point-in-time recovery |

## 2. The big idea

Everything that runs in an environment is written in Git. Changing what runs means changing Git
through a reviewed pull request; Argo CD then makes the cluster match. A new version of an agent
never goes straight to everyone. It goes to staging, then to one in ten conversations in
production, and the platform watches how it does there. If it fails more, costs more or is slower
than the version it would replace, the platform stops using it on its own, at once. Only when it
has done as well does a second pull request make it the version everyone gets.

> *Analogy:* a restaurant changing a recipe. The new dish is cooked in the test kitchen (staging),
> then offered to one table in ten (canary). If those tables send it back more often, it comes off
> the menu that evening without waiting for the owner. Only when it has done well does it replace
> the old dish on every menu, and that change is written into the menu book (Git).

## 3. What we're doing

1. Describe the services for Kubernetes once (the base) and what differs per environment
   (overlays): namespace, replicas, endpoints, OpenBao role, image digests, autoscaling.
2. Build each service image once per commit; scan, sign and publish it by digest.
3. Let several API replicas share runs safely (leases), so the API can be highly available.
4. Treat agent versions as configuration: per environment, which version is stable and which is
   being tried (canary or shadow), with an online-evaluation gate and feature flags.
5. Promote by pull request only, with rules that keep the order dev → staging → canary → prod.
6. Back up the agent database continuously and rehearse the restore, measuring how much is lost
   and how long it takes.
7. Prove it on a real cluster: kind with Argo CD, in CI.

## 4. Why we're doing this

Up to now the platform ran in Docker Compose on one machine. Production needs more than one copy
of each service, a controlled way to change what runs, and a way back when something goes wrong.
Agents make this harder than ordinary software. A new prompt or model can pass every offline test
and still behave worse on real traffic, so the safety net has to be in production itself: a small
share of real runs, scored as they finish, with a rollback that doesn't wait for a person.

> *Watch out:* "rolled back automatically" must also mean "Git is told". The platform stops the
> candidate on every replica within seconds, but Git still names it until someone opens the
> pull request that removes it (`just promote <agent> --abort`). The alert and runbook make sure
> someone does.

## 5. Key ideas

- **Base and overlays (Kustomize).** One description of the services, hardened the same way as in
  compose (non-root, read-only filesystem, no capabilities, seccomp, memory limits, no API token).
  Small per-environment changes on top. `just deploy-check` renders all three and applies
  Kubernetes schemas and the platform's own rules: digests, Pod Security "restricted", no
  secrets, nothing exposed, deny-by-default network policy, replicas and autoscaling per
  environment, environments named apart.
- **Secrets only from OpenBao, in the cluster too.** Pods log in to OpenBao with a 10-minute
  service-account token meant only for OpenBao. Connection strings in config are
  `${secret:...}` references, resolved at start-up. There are no Kubernetes Secret objects in Git.
- **Images by digest, signed.** The `images` workflow builds each service once per commit, attaches
  an SBOM and provenance, fails on fixable critical vulnerabilities, signs keylessly and records
  the digest. Overlays reference nothing else.
- **Run leases.** Each unfinished run is driven by one API replica, the one holding its lease.
  Others forward requests for it and read its events from the shared store. If a replica stops,
  its leases lapse and another continues its runs from their checkpoints.
- **Releases.** `deploy/releases/<env>.yaml` names each agent's stable version and, in prod, a
  candidate: a canary that serves 10% of new conversations, or a shadow that also runs a share of
  conversations with no side effects. Every candidate run, and a sample of stable runs by risk
  tier, is scored as it finishes. A breach of the gate rolls the candidate back on every replica,
  audited, counted and alerted.
- **Snapshots and promotion.** A version leaves dev as an unchangeable snapshot in `deploy/specs/`.
  `just promote` writes each step; CI's promotion check enforces the order. Prod's releases and
  snapshots need the named approver.
- **Point-in-time recovery.** A base backup plus every WAL segment archived off the host lets the
  database be rebuilt to its last archived moment. The recovery point objective (RPO) bounds how
  much can be lost; the recovery time objective (RTO) bounds how long recovery takes.

## 6. How to do it

```bash
just deploy-check                       # render, validate and check dev, staging, prod
just deploy-images prod <commit>        # pin an environment to a commit's signed images
just promote invoice-triage --to staging            # snapshot and serve in staging
just promote invoice-triage --to prod               # a 10% canary in prod (--shadow for shadow)
just promote invoice-triage --to prod --full --api https://agents.example/  # after the online eval passed
just promote invoice-triage --abort                 # remove prod's candidate
just promotion-check --base origin/main # what CI checks on a pull request
just dr-drill                           # destroy, restore, verify, measure
just kind-e2e                           # on a machine that can run kind (CI: deploy-e2e)
```

**Releasing a new agent version.**
1. Merge the change to `agents/<agent>/` with its new version: dev runs it.
2. `just promote <agent> --to staging --commit`, open the pull request. CI runs the agent's eval
   gates.
3. After staging, `just promote <agent> --to prod --commit`: a 10% canary, reviewed by the named
   approver.
4. Watch `GET /v1/admin/releases`. When the online evaluation says `pass`, run
   `just promote <agent> --to prod --full --api ...` in a pull request. If it rolled back, fix the
   agent and release a new version.

## 7. Benefits

- **Nothing changes in an environment without a reviewed pull request,** and the cluster is
  brought back to Git if someone changes it by hand (Argo CD self-heal).
- **A bad agent version reaches few people for a short time.** At most its canary share, and only
  until its gate has enough runs to judge.
- **The API survives losing a replica.** Its runs continue on another one, from their checkpoints.
- **Recovery is rehearsed and measured,** not assumed.
- **Every image that runs is traceable** to the commit, its SBOM, its scan and its signature.

## 8. Drawbacks and risks

- **More moving parts:** Argo CD, KEDA, a Temporal cluster, External Secrets, an object store for
  WAL. Each is pinned and documented, but each must be run.
- **The gate judges what it can measure online:** success, cost and time. A candidate that is
  worse in a way only a person would notice passes. That is why risk-tier sampling and
  review-after remain.
- **The measured RTO is for a small database on one host.** Real restores take longer with size;
  re-measure on the real environment.
- **Durable (Temporal) agents are released whole.** A canary for them needs Temporal worker
  versioning.
- **One maintainer.** GitHub doesn't let anyone approve their own pull request, so promotion to
  prod needs a second approver.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Kustomize | Helm | Packaging the platform for others to install with many options |
| Argo CD | Flux | A team already running Flux; same GitOps model |
| Agent-level canary in the run API | Argo Rollouts (image-level canary) | Canarying the platform's own code rather than an agent version; both can coexist |
| KEDA on the Temporal backlog | HPA on CPU | Work that is CPU-bound rather than queue-bound |
| Run leases in Postgres | Sticky sessions at the load balancer | One region, few replicas, and acceptance that a lost replica stops its runs until restart |
| WAL archiving and PITR | Nightly logical dumps | Small, rarely written data where a day of loss is acceptable |

## 10. Which approach fits which situation

- **A prompt or model change to an agent:** a new version, staging, then a 10% canary.
- **A risky change you can't afford to show anyone yet:** a shadow candidate (no side effects,
  nothing shown), then a canary.
- **Switching off an agent or one of its tools quickly:** a feature flag in the releases file;
  in an emergency, the kill switch (Step 10).
- **A platform code change:** `just deploy-images` to a commit's images, through a pull request.
- **Data loss or corruption:** `docs/runbooks/dr-restore.md`.

## 11. Common mistakes

- Editing a snapshot in `deploy/specs/`. It is refused: release a new version.
- Promoting to prod a version staging doesn't serve, or making a canary stable without its online
  evaluation passing.
- Putting a password in config. Use a `${secret:...}` reference; `deploy-check` refuses literal
  secrets.
- Exposing a service with NodePort or LoadBalancer. Expose it through the cluster's ingress.
- Running the migration with the runtime's database role. The pre-sync Job has its own role.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| A new agent version goes dev → staging → 10% canary → prod by PR only | ✅ | `deploy/releases/` and `deploy/specs/` (7 agents snapshotted and released; `just promotion-check`: 7 snapshots, 14 releases, 0 problems). `test_promotion.py` (6 passed): a version walks the four steps; skipping the canary, a canary of something staging doesn't serve, a canary starting above 10%, an edited or removed snapshot, a snapshot not from dev, and a staging candidate are each refused. CI runs the check on every push and pull request; prod's releases and snapshots are governance paths (CODEOWNERS and the governance gate need the named approver); eval gates run for the agents a promotion touches |
| A failed online-eval gate rolls back automatically | ✅ | `test_releases.py` (9 passed): a broken canary fails its first 3 runs. The gate (minimum 3 runs) breaches at 100.0% vs 0.0% failures and rolls the canary back; the next canary threads run on the stable version; audited (`release.rolled_back`), counted, alerted (`AgentReleaseRolledBack`, promtool-tested), reported by `GET /v1/admin/releases`. The rollback is shared through Postgres across replicas (integration test) |
| A DR drill restores state within the stated RPO/RTO | ✅ | `governance/dr.yaml` targets RPO 300 s and RTO 1800 s. `just dr-drill` (`evals/results/dr/agent-database-20261017T002403Z.json`): 340 writes acknowledged in 90 s, then the database and its volume destroyed. Restored from base backup plus archived WAL. Audit chain intact (56 rows); 56 runs, 56 checkpoints and 56 memories recovered equal to what was written. Measured RPO 32.3 s, RTO 5.0 s |
| Overlays, Argo CD apps, HA, autoscaling, versioned specs, flags, canary and shadow | ✅ | `just deploy-check`: dev 32, staging 30, prod 30, Argo CD 6 resources valid against Kubernetes 1.37 schemas, 0 policy problems. Policy tests: 7 passed. Two-replica API tests: 4 passed (forwarding, idempotency across replicas, takeover of a paused and of a running run). Shadow and flags in `test_releases.py` |
| On a real cluster | see CI | `deploy-e2e`: kind with Argo CD and KEDA; admission of every overlay; dev synced by Argo CD; a run through agent-api; network policy enforced (`evals/results/deploy/kind-e2e.json` artifact). Real OrbStack Kubernetes with Project 0: VERIFY-ON-MAC |
| ADR | ✅ | ADR 0049 |

## 13. Recap

The platform now runs on Kubernetes the way production expects. Three environments are described
in Git and kept in step by Argo CD. Images are signed and pinned. API replicas share runs, and
workers scale with the queue. A new agent version reaches production through staging and a 10%
canary, each step a reviewed pull request checked for order. The platform watches the canary's
real runs and takes it out of service on its own the moment it does worse. The agent database is
continuously backed up, and the restore has been rehearsed and measured: 32 seconds of writes lost
and 5 seconds to a verified database, against targets of 5 and 30 minutes.
