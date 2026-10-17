# The results table reads only committed files

## What changed
`just results` no longer reads `evals/results/redteam/*.json`. Those are local outputs that
`.gitignore` excludes, so CI didn't have them and `just results-check` failed there. The
red-team numbers in the table now come only from the CI run recorded in
`docs/results/ci-evidence.yaml`.

## Why
The table has to come out the same on every checkout, and a local file that isn't committed
isn't evidence anyone else can check.

## How to check it
`just results-check` passes on a fresh clone (CI's `lint, types, unit` job).

## Risks and follow-ups
None.
