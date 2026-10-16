# Mechanics baseline for every reference app, checked in CI; judge calibration command

**Step:** 34 · **Commit:** feat(evals): mechanics baseline checked in CI; judge calibration command · **Date:** 2026-10-16

## What changed
- **Baseline decision (owner delegated, 2026-10-16).** Commit a *mechanics* baseline now and run the real-model baseline on the Mac.
- **`evals/baselines/<app>.mechanics.json`.** One per reference app, from the stand-in gateway's canned mock responses. Each file:
  - is labelled `kind: mechanics`, with a note that it says nothing about model quality;
  - keeps only stable facts: dataset hash, versions, provider, and per-task success and status.
- **`just eval-baseline` / `just eval-baseline-check`.** The check re-runs every app and fails on any task that stopped passing, or if the task set changed without a new baseline. CI's integration job runs it.
- **`scripts/eval_app.py calibrate <report> <rubric>`.** Grades every item of a real-model report with the rubric's judge and compares the verdicts with `evals/labels/<rubric>.jsonl`. It writes `evals/results/calibration/<rubric>.json`. It refuses mock-run reports; without human labels the result is "not measured".

## Why
Step 34 asks for "a baseline is committed". With mock models, a quality baseline would be fiction. A mechanics baseline is honest, and still worth having: it pins the deterministic behaviour (hello-workflow, invoice validation and holds, oversized-batch refusal), so a regression there fails CI today.

## How to check it
These are the stand-in results; they are not quality numbers:

| App | Passed | Why |
|---|---|---|
| hello-workflow | 100/100 | Its real result: the app uses no model |
| invoice-triage | 85/115 | 35 valid invoices pass the deterministic path and 50 held invoices are held; the 30 that need a model fail |
| batch-summary | 6/100 | The 6 oversized batches are refused up front |
| policy-debate, research-report, review-chat, support-handoff | 0/100 each | They need a real model |

All 715 tasks ran in 3 min 10 s locally, and a second run showed 0 regressions. `uv run pytest tests/scripts/test_eval_label_sheet.py` checks that calibration says "not measured" without labels and refuses mock reports.

## Risks and follow-ups
- **Real-model baseline (VERIFY-ON-MAC):** `just eval-app <app> --provider gateway --publish` for each app.
- **Judge agreement:** needs people to label a real-model report (`just eval-label-sheet`), then `calibrate`. Labels can't be generated here.
