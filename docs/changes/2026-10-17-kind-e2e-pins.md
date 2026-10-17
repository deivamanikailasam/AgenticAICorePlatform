# kind e2e deploys the images Git pins

## What changed
`scripts/kind_e2e.py` no longer overrides the dev overlay's images with the commit's own images.
Argo CD deploys exactly what the overlay pins, and the smoke client uses the same pinned
agent-api image.

## Why
What Git pins is what runs (GitOps). Also, a commit that only changes manifests builds no
images, so waiting for that commit's images would never finish.

## How to check it
The `deploy-e2e` run: its "dev synced" step lists the pinned images.

## Risks and follow-ups
New service code is exercised on a cluster once its images are pinned (`just deploy-images`);
until then the integration job covers it.
