# The promotion rules judge a push commit by commit

## What changed
- `ReleaseRepo.check_each(base)` applies the promotion rules to every commit from `base` to
  `HEAD` that touches the agents or the deployment, each against its parent.
- `just promotion-check --base REF`, used by CI, now calls it, and each problem names its
  commit.
- For a pull request, CI checks the merge commit against its base, so a pull request is still
  judged as one change, as before.

## Why
Fast-forwarding `main` by 161 commits made CI compare `main`'s old head (from Step 18) directly
with the new one. The rules are about single steps:
- a snapshot is taken from dev;
- a first prod release is the version staging serves.

Across the whole range, they reported two problems that no single commit has. On the branch,
every push had passed the same rules step by step.

## How to check it
- `just promotion-check --base 2e5cfd5`: 0 problems; before this change, the same command
  reported 2.
- The new test `test_a_push_of_many_commits_is_judged_step_by_step` covers both sides:
  - a range that fails as one jump passes step by step;
  - a bad commit added to it (prod moved without a canary) is still caught and named.

## Risks and follow-ups
None. No rule changed. Every intermediate state of a push now has to be valid, which is
stricter than checking only its two ends.
