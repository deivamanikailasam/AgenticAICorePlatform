# agentctl deploy-check, and promotion checks from a base commit

## What changed
- `agentctl deploy-check` builds each overlay of a project (`kustomize build`, with the same
  load restrictor as the platform's deploy-check and Argo CD). It then applies the platform's
  deployment rules (`agent_governance.deploy_policy`): digests, Pod Security "restricted", no
  secrets, nothing exposed, deny-by-default network policy, replicas per environment, and
  environments kept apart.
- `agentctl deploy-config --check --base REF` checks the promotion rules from a base commit,
  which CI uses for pull requests.

## Why
The template's CI has to hold a project's deployment to the same rules as the platform's own.

## How to check it
`uv run pytest libs/agent-sdk`: an overlay exposing a NodePort Service fails `deploy-check`.
That test is skipped without kustomize.

## Risks and follow-ups
Overlays that use the platform's remote base need network access to GitHub to render.
