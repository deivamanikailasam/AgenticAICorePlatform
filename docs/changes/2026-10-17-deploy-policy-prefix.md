# Deployment rules for an agent project's own environments

## What changed
- `agent_governance.deploy_policy.problems(env, objects, prefix=...)` names the expected
  namespace `<prefix>-<env>`. The default stays `agent-platform`, so the platform's checks don't
  change.
- `agentctl deploy-check` passes the project's name, from its `pyproject.toml`.

## Why
A project from the Step 39 template runs the platform under its own namespaces
(`claims-desk-dev` and so on). Every rule applies there unchanged, except the namespace name.

## How to check it
- `uv run pytest libs/agent-governance/tests/test_deploy_policy.py`: a compliant prod
  environment named `claims-desk-prod` passes with `prefix="claims-desk"` and fails without it.
- `just template-e2e --local`: the generated project's `agentctl deploy-check` passes against
  the platform's remote base.

## Risks and follow-ups
The other separation rules (OpenBao role and mount, Keycloak client and `AGENT_ENV` naming the
environment) are unchanged.
