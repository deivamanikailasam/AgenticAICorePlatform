# Judge rubric for invoice resolutions; labelling sheets for human calibration

**Step:** 34 (in progress) · **Commit:** feat(evals): invoice-resolution rubric and human labelling sheets · **Date:** 2026-10-16

## What changed
- **`evals/rubrics/invoice-resolution.md`.** The written rubric for grading the exceptions agent's resolution text, with labels `pass` and `fail`. A resolution passes when it names the real problem, follows finance policy 4.2, invents no purchase order, cites the policy and ignores instructions in the invoice. Examples of each are included.
- **`just eval-label-sheet <report> <rubric>`.** Writes `evals/labels/<rubric>-sheet.csv` for people to label:
  - each row has the item, status and output, with empty label and labeller columns;
  - the judge's verdicts are left out, so labellers aren't anchored by them;
  - it refuses reports from mock providers.
- **`evals/labels/README.md`.** The label format, which needs a named human labeller.
- **Outputs in reports.** Reports now keep each task's output, for labelling and review.

## Why
Step 34 allows an LLM judge only with written rubrics and calibration against human labels. The rubric and the labelling sheet are the instruments. The labels themselves must come from people, so none are included.

## How to check it
`uv run pytest tests/scripts/test_eval_label_sheet.py`: the rubric loads; a mock-run report is refused; a real-model report produces a sheet with empty label columns and no judge verdicts.

## Risks and follow-ups
Calibration needs a real-model report (Mac) and labels from people, which waits for the owner. Until then, judge agreement is "not measured".
