# The high-risk merge gate in CI

**Step:** 32 · **Commit:** ci(governance): high-risk changes need the named approver · **Date:** 2026-10-15

## What changed
- `.github/workflows/governance.yml` runs `scripts/governance_gate.py` on pull requests and on reviews. The check is named "high-risk approval".
- The check fails when a pull request changes either of these, until one of the `high` approvers in `governance/approvers.yaml` approves the pull request's latest commit:
  - a spec that is high-risk before or after the change (raising, lowering or deleting all count);
  - a governance file.
- The workflow runs the base branch's copy of the gate and reads the base branch's approvers. A pull request therefore can't edit the gate or name its own approver.
- Review handling:
  - Only each reviewer's latest review counts.
  - An approval of an older commit doesn't count.
  - Authors can't approve their own change.

## Why
Step 32's "Done when": "a high-risk spec change cannot merge without the named approver". CODEOWNERS alone only requests reviewers. This check turns the rule into a status that branch protection can require.

## How to check it
`uv run pytest tests/scripts/test_governance_gate.py`, on a real git history with GitHub's reviews API mocked:
- A high-risk change fails with no review, with a stranger's approval, and with the approver's approval of an older commit. It passes once the approver approves the head commit.
- A pull request that makes its author the approver still needs the base branch's approver.
- A low-risk change passes with no review.

## Risks and follow-ups
- **Owner action:** in the repository settings, make "high-risk approval" a required status check on `main`. That is a GitHub setting, not code. Until it is set, the check reports but doesn't block.
- Team entries (`@org/team`) in `high` are not yet expanded into members. The gate counts named users only, and listing a team doesn't widen who may approve.
- The end-of-build merge to `main` is a direct merge, not a pull request, so the gate doesn't run on it. The current specs are low and medium risk.
