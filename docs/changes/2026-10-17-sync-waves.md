# Argo CD sync order: migrations after their service account and config

## What changed
- The migration Job is now an Argo CD **Sync** hook in wave -1 (it was a PreSync hook).
- Wave -3: the namespace. Wave -2: service accounts, network policies and the generated
  ConfigMaps. Wave 0 (default): Deployments and the rest. Argo CD waits for each wave, so the
  migrations finish before the services start.

## Why
The deploy-e2e run showed that a PreSync hook runs before the sync creates anything. On a first
sync the Job's service account and environment ConfigMap did not exist, so its pod was never
created. Admission, the stand-in bridge and OpenBao's Kubernetes auth all passed in that run.

## How to check it
`just deploy-render dev` shows the wave annotations. The next `deploy-e2e` run gets past the
sync.

## Risks and follow-ups
None. Re-syncs keep the same order, and the Job is recreated each time (`BeforeHookCreation`).
