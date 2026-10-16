# agent-evals: task schema, deterministic checkers, rubric judge with calibration, versioned results

**Step:** 34 (in progress) · **Commit:** feat(agent-evals): checkers, rubric judge with calibration, versioned results · **Date:** 2026-10-16

## What changed
New library `libs/agent-evals`:
- **`tasks.py`.** The golden-task schema (`evals/apps/<app>/tasks.jsonl`). Each task names what it checks, and only those checks are scored:
  - expected status;
  - answer checks: contains, not contains, regex, equals, or a number with a tolerance;
  - trajectory: expected tools in order, required and forbidden tools, allowed extra calls, expected argument values;
  - policy: tools that must ask for approval, tools that must be denied, no guardrail blocks;
  - limits: steps, tokens, AUD, latency;
  - an optional judge rubric.
- **`checks.py`.** Deterministic checkers over an `Observation` of a run, built from what the platform recorded rather than what the model claimed. They score task success, final-answer correctness, trajectory order, unnecessary steps, required and forbidden tools, tool-call accuracy (arguments validated against the tool's schema), policy compliance and limits. Each score says why it failed.
- **`judge.py`.** An LLM judge that works only from a written rubric (`evals/rubrics/*.md`, with labels and criteria). It runs at temperature 0, and its answer is constrained to the rubric's labels; anything else is an error.
  - **Calibration** compares the judge's labels with human labels (`evals/labels/<rubric>.jsonl`) and reports agreement and Cohen's kappa with the item count and labellers.
  - It refuses any label line without `labeller_kind: human` and a named labeller.
  - With no human labels it reports "not measured", never a number.
- **`results.py`.** Reports record:
  - the app's versions (the audit trail's identifiers);
  - the task set's SHA-256 and size;
  - the model provider (`scripted`, `stand-in` or `gateway`);
  - the commit, the date, every task's scores, and the aggregates (success rate, per-metric mean and pass count, mean steps, tokens, AUD, latency p50 and p95).

  `compare` lists version changes, metric changes, regressions and fixes. `publish`, which writes the agent card's eval line, refuses any run that didn't use real models.

## Why
Step 34: deterministic checkers first, a judge only with written rubrics and calibration against human labels, and results stored with versions for comparison. The guard rails exist so the harness itself can't produce an invented number:
- a model's labels can't calibrate a model;
- a mock run can't become a baseline or appear on an agent card.

## How to check it
`uv run pytest libs/agent-evals` (14 tests):
- every check passes on a correct run, and names each failure on a wrong one;
- out-of-order calls and invalid arguments are caught;
- the judge only accepts rubric labels;
- kappa matches a hand-computed example (0.75 observed, 0.5 kappa);
- non-human labels are refused, and with no labels agreement is "not measured";
- a mock run can't be published;
- comparison finds regressions.

## Risks and follow-ups
Step 34 is not done yet. Still to do:
- the runner through the run API and the task sets (next commits);
- judge calibration, which waits for human labels from the owner;
- the baseline, which waits for a real-model run (owner's decision, VERIFY-ON-MAC).
