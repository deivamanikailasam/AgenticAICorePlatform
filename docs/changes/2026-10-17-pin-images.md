# Overlays pinned to the images of 4ff80dc

## What changed
dev, staging and prod now pin agent-api, agent-worker, mcp-gateway, a2a-gateway and ops to the
digests the images workflow pushed, signed and scanned for commit 4ff80dc. Those images include
the migration script for the pre-sync Job, run leases, releases and Kubernetes auth to OpenBao.
Until now they pinned f1ee58d.

## Why
The pre-sync migration Job and the release-aware agent-api need code that f1ee58d's images don't
have.

## How to check it
`just deploy-check` (0 problems). The images workflow run for 4ff80dc lists the same digests.

## Risks and follow-ups
Later image bumps go through `just deploy-images <env> <commit>` in a pull request; prod's is a
governance path (named approver).
