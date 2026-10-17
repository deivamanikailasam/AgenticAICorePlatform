# Promotion by pull request: dev → staging → 10% canary → prod

## What changed
- **Released versions are snapshots.** `deploy/specs/<agent>/<version>/` is a copy of
  `agents/<agent>/` (spec and workers), taken while that is the version on main. It never changes.
  All seven reference agents are snapshotted at their current versions.
- **Each environment's releases.** `deploy/releases/staging.yaml` and `prod.yaml` say what serves
  there. Staging and prod now mount exactly those versions (generated
  `deploy/overlays/<env>/released`):
  - agent-api gets every named version plus the releases file, so it can serve a candidate;
  - agent-worker and a2a-gateway get the stable versions;
  - dev keeps mounting `agents/` at main's head (`deploy/components/head-specs`).
- **`just promote`** writes each step: `--to staging`; `--to prod` (a 10% canary, `--shadow`, or
  an agent's first release); `--to prod --full`; `--abort`. `--full` refuses unless the running
  API's online evaluation of the canary says `pass` and it was not rolled back. `--commit` puts the
  change on a `promote/<agent>-<version>-<env>` branch for a pull request.
- **`just promotion-check`** (CI `deploy` job, base commit against head) enforces
  `agent_governance.promotion`:
  - snapshots are immutable and come from dev;
  - staging has no candidates;
  - a prod candidate is the version staging serves and starts at 10% (or in shadow);
  - a version becomes prod's stable only by replacing the candidate it was;
  - an agent's first prod release comes from staging.
  - Policy: `deploy/promotion.yaml`.
- **Approvals.** `deploy/releases/prod.yaml`, `deploy/specs/`, `deploy/overlays/prod/` and
  `deploy/promotion.yaml` are governance paths: CODEOWNERS and the governance gate require a named
  high approver.
- **Eval gates.** Eval gates on a pull request now run for an agent whose snapshot changed, and
  for every agent when a releases file changed.

## Why
Step 38 Done-when: a new agent version goes dev → staging → 10% canary → prod by pull request
only, with eval gates and approvals. Versioned specs are deployed as config.

## How to check it
- `uv run pytest libs/agent-governance/tests/test_promotion.py`: 6 passed:
  - a version walks the four steps;
  - skipping the canary, a canary of something staging doesn't serve, a 50% start, an edited or
    removed snapshot, one not from dev, and a staging candidate are each refused with a reason;
  - the repository's releases are consistent.
- `uv run pytest services/agent-api/tests/test_releases.py`: the repository's staging and prod
  releases load as agent-api loads them.
- `just promotion-check`: 7 snapshots, 14 releases, 0 problems.
- `just deploy-check`: prod's agent-api mounts `/app/versions` and `/app/releases`; worker and
  gateway mount `/app/agents`.

## Risks and follow-ups
- The online-eval check before `--full` runs against the live API (operator token). CI cannot
  see production, so the pull request should carry the verdict `just promote` printed.
- Branch protection must require the `ci` and governance checks on `main`; that is the owner's
  setting. A second approver is still needed, because GitHub forbids approving your own pull
  request.
