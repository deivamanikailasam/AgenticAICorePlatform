# Promotion and deployment config as a library

## What changed
- `agent_governance.release_repo.ReleaseRepo(root)` now holds what `scripts/promote.py`,
  `scripts/promotion_check.py` and `scripts/deploy_config.py` did:
  - read the deployment state at a commit;
  - make each promotion step (staging, prod canary or shadow, full rollout after a passing online
    evaluation, abort);
  - fetch the online-eval verdict;
  - render the generated deployment config;
  - commit a step on a `promote/` branch.
- The three scripts are now thin command lines over it, with the same output.
- A new test runs the whole flow in a fresh repository with the template's layout.

## Why
Step 39's `agentctl promote` runs in agent projects outside this repository. Moving the code into
a library lets those projects use the same rules and files instead of a copy.

## How to check it
- `just deploy-config-check`: 8 generated files, up to date (unchanged by the move).
- `just promotion-check`: 7 snapshots, 14 releases, 0 problems.
- `uv run pytest libs/agent-governance`: includes `test_release_repo.py`, in which a project
  walks dev → staging → canary → prod and is refused a skipped staging and an edited snapshot.

## Risks and follow-ups
None in behaviour. A missing releases file now reads as empty, where the old renderer failed.
