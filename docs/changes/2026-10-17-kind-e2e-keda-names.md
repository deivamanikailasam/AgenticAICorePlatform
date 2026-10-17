# kind e2e waits for KEDA's real deployments

## What changed
The e2e waits for `keda-operator`, `keda-metrics-apiserver` and `keda-admission`: the names in
KEDA 2.21.0's manifest. It previously waited for a `keda-admission-webhooks` deployment that
doesn't exist.

## Why
The first deploy-e2e run created the cluster and installed Argo CD and KEDA. It then failed
waiting for that deployment.

## How to check it
The next `deploy-e2e` run gets past the add-ons step.

## Risks and follow-ups
None.
