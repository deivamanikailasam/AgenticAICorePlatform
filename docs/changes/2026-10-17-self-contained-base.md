# The deployment base builds on its own

## What changed
- `deploy/base` now mounts `deploy/base/data-lifecycle.yaml`, a copy of
  `governance/data-lifecycle.yaml` that `just deploy-config` writes. It no longer reads
  `../../governance/` directly.
- `just deploy-config-check` fails when the copy is stale (now 9 generated files).

## Why
Agent projects made from the Step 39 template use the platform's base as a remote kustomize
resource at a pinned commit. Kustomize refuses files outside a remote base's directory,
whatever `--load-restrictor` says, so the base must not reach outside it.

## How to check it
- `just deploy-config-check`: 9 generated files, up to date.
- `just deploy-check`: 3 environments, 0 problems.

## Risks and follow-ups
`governance/data-lifecycle.yaml` stays the source; the copy changes only through
`just deploy-config`.
