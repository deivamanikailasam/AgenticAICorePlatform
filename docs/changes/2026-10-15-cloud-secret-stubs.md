# Cloud secret managers and workload identity as documented stubs; ADR 0040

**Step:** 29 · **Commit:** feat(agent-tools): cloud secret managers and SPIFFE identity as documented stubs · **Date:** 2026-10-15

## What changed
- `credential_broker.aws`, `credential_broker.gcp` and `credential_broker.azure` are registered stubs. The AWS Secrets Manager, GCP Secret Manager and Azure Key Vault resolvers are stubs too.
- `SpiffeJwtAuth` (Kubernetes workload identity logging in to OpenBao) is a stub.
- Every stub refuses with a pointer to ADR 0040, and tests pin that behaviour.
- ADR 0040 records the Step 29 design and its alternatives.

## Why
The spec lists two alternatives:
- "Keycloak + OpenBao → cloud IAM and secret managers";
- "Optional workload identity (SPIFFE/SPIRE) in Kubernetes".

This build has neither a cloud account nor a cluster, so these can't be built or verified here. The rule for switchable alternatives is a documented stub with an ADR and a test.

## How to check it
`uv run pytest libs/agent-tools/tests/test_cloud_secret_stubs.py`

## Risks and follow-ups
Step 37 (production) finishes the one matching the target platform:
- AWS: STS or RDS IAM auth.
- GCP: IAM Credentials tokens.
- Azure: managed identity.
- Kubernetes: SPIRE.
