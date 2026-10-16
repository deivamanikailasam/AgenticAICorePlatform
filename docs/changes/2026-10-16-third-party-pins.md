# Third-party images for Argo CD and Temporal pinned

## What changed
- `deploy/third-party-images.txt` now pins Argo CD v3.5.3, Dex v2.45.1, Redis 8.2.3-alpine and the
  Temporal cluster images (server and admin-tools 1.32.0, UI 2.54.1), using digests that the
  `images` workflow resolved against the registries. Redis comes from Docker Hub rather than
  public.ecr.aws.
- The workflow tries each registry lookup three times before failing.

## Why
The first `images` run built and signed all five service images, but its third-party job hit a
public.ecr.aws rate limit (HTTP 429). It still printed the Argo CD and Dex digests, which are now
pinned.

## How to check it
The `images` workflow's third-party job passes. The pinned digests appear in its summary, and
the KEDA images are printed for pinning.

## Risks and follow-ups
- KEDA images are still `resolve` lines until their digests are pinned with the KEDA scaler.
