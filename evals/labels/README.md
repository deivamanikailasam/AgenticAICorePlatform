# Human labels for judge calibration (Step 34)

One file per rubric, `evals/labels/<rubric>.jsonl`, one line per labelled item:

```json
{"item": "gst-error-INV-2036", "label": "pass", "labeller": "Jane Citizen", "labeller_kind": "human", "labelled_at": "2026-10-20"}
```

- `item` is the task id from the eval report that produced the output.
- `label` is one of the rubric's labels.
- `labeller` must name a person and `labeller_kind` must be `human`. The calibration refuses
  anything else: a model's labels can't calibrate a model.

Make a labelling sheet from a real-model report with
`just eval-label-sheet <report.json> <rubric>`. It writes every judged item (task, output,
rubric) to a CSV for people to fill in. Label without looking at the judge's verdicts.

No labels have been collected yet: judge agreement is "not measured" until they are.
