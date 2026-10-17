# A results table of measured numbers only

## What changed
- `docs/results.md` lists every number the platform has measured, each with its set size and
  the run or file it came from. It covers the mechanics baselines, CI runs (integration tests,
  the red-team suite, eval gates, deploy-e2e, template-e2e), the recorded red-team runs and the
  disaster-recovery drill.
- `just results` generates it from `evals/` and `docs/results/ci-evidence.yaml` (CI numbers,
  each with its workflow run ID). `just results-check`, part of `just check`, fails when it's
  stale.

## Why
Step 40 asks for a results table, and the build's rule is that no number appears without the
run that measured it. Generating the page from the recorded files keeps it honest as baselines
change.

## How to check it
`just results-check`. Open any linked workflow run and compare its log with the row.

## Risks and follow-ups
- Quality on real models is listed as not measured (VERIFY-ON-MAC).
- CI numbers are copied from logs by hand into `ci-evidence.yaml`; a wrong copy would show up
  against the linked run.
