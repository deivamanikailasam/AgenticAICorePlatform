# Eval gates in CI: on every pull request, nightly, and before a release

**Step:** 36 (in progress) · **Commit:** ci(evals): eval gates on pull requests, nightly and in the release gate · **Date:** 2026-10-16

## What changed
- **`ci.yml`, integration job.** After the integration tests, the red-team suite runs first, then the eval gate.
  - On a pull request, the gate judges the apps the change touches (against `origin/<base>`). It reads approvals from the pull request's reviews and posts one updated comment with the results.
  - On a push, it judges every app.
  - The job fails if the red team finds a blocking issue, or if any app is blocked or still waits for an approval. The checkout has full history so the base branch can be compared.
  - `just eval-baseline-check` is replaced: the gate's regression checks cover it.
- **`release.yml`.** It also runs every app's eval gate on the release commit and refuses the release unless all pass. It can now be called by other workflows.
- **`nightly.yml` (new).** Every night it runs the same full job as a release: every app's gate, the whole red-team suite including gVisor, both attack libraries, issue sync, and the release verdict.

## Why
Spec Step 36 says the gates run on every pull request that touches specs, prompts, tools, models or patterns, the full suites run nightly, and results are posted to the pull request.

## How to check it
Locally, with the stand-in, I simulated a change that removed invoice-triage's GST check (then restored the file). `just eval-gate invoice-triage` was **blocked** and exited 1:
- 5 tasks that passed in the baseline now failed (at most 2 allowed);
- success fell from 73.9% to 69.6% (−4.3 points; at most −2 allowed).

The real pull-request comment and review approvals are exercised the first time a pull request is opened against `main`.

## Risks and follow-ups
- The pull-request comment needs the job's `pull-requests: write` permission. A pull request from a fork gets a read-only token, so the comment step fails there; the gate itself still runs.
- To approve a cost increase, the approver submits an approving review containing `/approve-cost`, then re-runs the integration job. A review doesn't re-run CI by itself.
