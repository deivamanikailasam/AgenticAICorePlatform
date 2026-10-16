# Kubernetes environments: Kustomize base, dev/staging/prod overlays, Argo CD apps

## What changed
- `deploy/base`: agent-api, agent-worker, mcp-gateway and a2a-gateway as Deployments with the
  compose hardening carried over:
  - non-root, read-only root filesystem, no privilege escalation, all capabilities dropped,
    the runtime's seccomp profile;
  - no Kubernetes API token mounted, only a 10-minute OpenBao token (audience `openbao`);
  - memory limits, probes, disruption budgets, spread across nodes;
  - a pre-sync migration Job with its own DDL credential;
  - deny-by-default network policies.
- Agent specs ship as a ConfigMap generated from `agents/` (`just deploy-config`, checked in
  `just check`). They are mounted at the same tree the services read.
- `deploy/overlays/{dev,staging,prod}`, each with:
  - its own namespace, with Pod Security `restricted` enforced;
  - its own environment, OpenBao role and mount, and Keycloak client;
  - database DSNs as `${secret:...}` references;
  - images by digest from the `images` workflow (`just deploy-images`).
- The overlays differ as follows:
  - dev runs one replica of everything and a Temporal dev server;
  - staging runs 2 replicas, prod 3 (`deploy/components/cluster`);
  - in staging and prod, workers scale on the Temporal task-queue backlog with KEDA (staging
    1–4, prod 2–10), and pods go to the Temporal cluster in `temporal-<env>`.
- `deploy/argocd`:
  - an AppProject limited to this repo and Temporal's chart, the platform and Temporal
    namespaces, and cluster-scoped Namespaces only;
  - an Application per environment, following `main`;
  - Temporal Helm chart 1.7.0 at a pinned commit, with images by digest and 2 or 3 replicas;
    its database password comes from OpenBao through External Secrets.
- `just deploy-check` (new CI job `deploy`, tools checksum-verified) does the following:
  - renders every overlay;
  - validates it with kubeconform against Kubernetes 1.37 schemas pinned to a commit;
  - applies `agent_governance.deploy_policy`: digests; Pod Security; no API token, Secret
    objects or literal secrets; no NodePort or LoadBalancer; deny-by-default; memory limits;
    replicas, disruption budgets and worker autoscaling per environment; environments named
    apart.
- `scripts/migrate.py` resolves an OpenBao DSN reference when run in a service image. The service
  image now includes it.

## Why
Step 38 needs dev, staging and prod overlays, Argo CD apps, worker autoscaling on queue depth,
and HA for the API, workers, Temporal and the MCP gateway. The spec's security defaults hold in
the cluster: digests, least privilege, secrets only from OpenBao, nothing exposed.

## How to check it
- `just deploy-check`: 3 environments, 0 problems. dev 32, staging 29 and prod 29 resources are
  valid, as are 6 Argo CD resources. The Temporal namespaces have 2 valid resources and
  SecretStore is skipped (kubeconform 0.8.0 finds no schema for it).
- `uv run pytest libs/agent-governance/tests/test_deploy_policy.py`: 7 passed. Each rule refuses a
  manifest that breaks it.

## Risks and follow-ups
- agent-api replicas do not yet share runs. A run is driven, and streamed, by the replica that
  created it. Run leases and forwarding come next; until then staging/prod replicas need sticky
  routing.
- These are prerequisites outside this repo (VERIFY-ON-MAC):
  - Project 0's namespace and service names (`rag-platform`);
  - OpenBao Kubernetes auth roles and the per-environment KV mounts;
  - External Secrets Operator and KEDA installed;
  - the Temporal databases.
- KEDA's images are still `resolve` lines; they are pinned when KEDA's install is added.
- The overlays pin images from commit f1ee58d. Those images predate the migration script, so
  they are re-pinned once this commit's images are built.
