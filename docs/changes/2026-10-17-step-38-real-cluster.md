# Step 38: the real-cluster check passes

## What changed
The Step 38 chapter's "On a real cluster" row, ADR 0049's consequences and PROGRESS now record
the passing `deploy-e2e` run and the image pin it ran on.

## Why
Step 38's last open check was the platform on a real cluster.

## How to check it
GitHub Actions, `deploy-e2e` run 38996375350 on e1ba0ad: 7 of 7 checks passed.
- kind with Argo CD and KEDA;
- server-side admission of every overlay;
- the stand-in bridged;
- OpenBao Kubernetes auth;
- dev synced and healthy;
- a run through agent-api;
- a namespace the ingress policy doesn't admit can't reach agent-api.

The cluster steps took 2 min 59 s. Its `kind-e2e` artifact holds the step results.

## Risks and follow-ups
Real OrbStack Kubernetes with Project 0 remains VERIFY-ON-MAC.
