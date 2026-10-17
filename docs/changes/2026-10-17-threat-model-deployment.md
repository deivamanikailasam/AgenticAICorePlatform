# Threat model: the deployment and release pipeline

## What changed
- A new component in `governance/threat-model.yaml`, "Environments, releases and promotion",
  covers all six STRIDE categories:
  - T50: a tampered or unpinned image runs;
  - T51: a version skips review, staging or its canary;
  - T52: a pod reaches the Kubernetes API, its host or other namespaces;
  - T53: secrets leak through manifests;
  - T54: a worse agent version keeps serving;
  - T55: losing a replica or the database loses data;
  - T56: promotions and rollbacks can't be traced.
- New controls in `governance/controls.yaml`, each with its evidence and tests:
  - C23: a hardened deployment;
  - C24: promotion by pull request in a fixed order;
  - C25: online evaluation with automatic rollback;
  - C26: recoverable service and data.
- `docs/security/threat-model.md` and `control-matrix.md` are regenerated.

## Why
Step 37's rule: revisit the threat model when components change. Step 38 adds the cluster, the
release files and the promotion path.

## How to check it
`just threat-check`: 56 threats, 12 components, 26 controls, 66 tests, 0 problems.

## Risks and follow-ups
The framework mapping stays at the level of the dated sources (`compliance-sources.md`). Legal
review still applies.
