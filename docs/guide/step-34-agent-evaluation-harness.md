# Step 34 · Agent evaluation harness

## 1. At a glance

| | |
|---|---|
| **Goal** | Know how well each agent does its job, on hundreds of known cases, with numbers that can be trusted and compared over time |
| **Time** | About two days |
| **You end up with** | `libs/agent-evals` (task schema, checks, judge and calibration, results); `agent_api.eval_runner` (tasks through the run API); harnesses for all 7 reference apps; 715 golden tasks; a mechanics baseline checked in CI; `just eval-app`, `eval-compare`, `eval-baseline-check`, `eval-label-sheet`, `eval-tasks` |
| **New words** | golden task, deterministic check, trajectory, LLM-as-judge, rubric, calibration, Cohen's kappa, baseline |

## 2. The big idea

Agents fail quietly: a confident, wrong answer looks like success. So you test them like a new
employee:
- on many cases where the right outcome is already known;
- checking what they actually did (which tools, which approvals, which colleagues they asked),
  not just what they said.

Most outcomes can be checked by code. For the rest, such as "is this report well grounded?", a
second model grades against written rules. You only trust that grader as far as it agrees with
people on the same items.

> *Analogy:* a driving test. The examiner has a fixed route (golden tasks) and a checklist
> (deterministic checks): did they stop at the sign, signal, stay under the limit. A few things,
> like "drove smoothly", are judgement calls, so examiners are trained against each other until
> they agree (calibration).

## 3. What we're doing

1. Define golden tasks and what a correct run looks like.
2. Score each run from the platform's records: status, answer, tools, approvals, delegations,
   cost and time.
3. Generate at least 100 tasks for each of the 7 reference apps.
4. Run every task through the run API with the app's real tools.
5. Add a rubric-based judge whose agreement with human labels can be measured.
6. Store results with versions, compare runs, and commit a baseline.

## 4. Why we're doing this

Without evals, every prompt tweak, model swap or upgrade is a guess. With them, a change shows
up as "3 tasks regressed, here's which".

The numbers also go on agent cards (Step 32) and into release decisions (Step 38), so they have
to be honest: measured on a named task set, with versions and sample size.

> *Watch out:* an eval number without its dataset, its size and the model that produced it
> means nothing. A number from mock models presented as quality is worse than no number.

## 5. Key ideas

- **Golden task.** An input plus what must be true of a correct run. Here every task is built as
  a known case, so its expected outcome follows from how it was built.
- **Deterministic checks**, scored from the audit trail and the ledger:
  - task success (end state);
  - answer correctness;
  - trajectory (expected tools in order, unnecessary steps, required and forbidden tools);
  - tool-call accuracy (arguments valid for the tool's schema);
  - policy compliance (approval shown with the right amount, payments that must never be
    proposed);
  - team trajectory (the right workers, how often);
  - limits (steps, model calls, tokens, AUD, latency).
- **LLM-as-judge.** A model grades only against a written rubric, and must answer with one of the
  rubric's labels.
- **Calibration.**
  - Compare the judge with people on the same items, using agreement and Cohen's kappa
    (agreement beyond chance).
  - Only labels from named people count.
  - With none, the result is "not measured".
- **Baseline.** A committed result to compare against.
  - The *mechanics* baseline uses mock models and pins deterministic behaviour.
  - The *quality* baseline needs real models.

## 6. How to do it

```bash
just eval-tasks                                     # regenerate the 7 task sets (seeded)
just eval-app invoice-triage --limit 20             # stand-in: harness check, report not kept in git
just eval-baseline-check                            # all apps vs the committed mechanics baseline (CI)
# on the Mac, with real models
just eval-app research-report --provider gateway --publish     # quality result, cited on the card
just eval-compare evals/results/research-report/A.json evals/results/research-report/B.json
just eval-label-sheet evals/results/invoice-triage/<report>.json invoice-resolution   # for people
uv run python -m scripts.eval_app calibrate evals/results/invoice-triage/<report>.json invoice-resolution
```

Adding an app means an `agent_platform.eval_apps` entry point that supplies the spec and tools,
plus a seeded generator.

## 7. Benefits

- Every change is measured on 715 known cases. Regressions are named task by task.
- Scores come from what ran, so a model can't talk its way to a pass.
- Policy is tested as behaviour. An agent manipulated into proposing a payment fails the task.
- The judge can't be trusted by default, and mock numbers can't leak onto agent cards.

## 8. Drawbacks and risks

- **Quality isn't measured here.** The models in this build are mocks, so quality waits for the
  Mac.
- **Judge agreement waits for people** to label real-model outputs.
- **Generated tasks** are clean and regular. Add real, messy cases from production review
  (Step 28 samples) over time.
- **CI time.** The baseline check costs about 3 minutes of integration time.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Checks over platform records | Final-text scoring (Promptfoo, OpenAI Evals) | Single-prompt features with no tools or policy |
| Generated tasks | Curated production samples | Once production traffic exists: add them alongside |
| Rubric judge with human calibration | Pairwise preference judging | Comparing two versions' writing quality head to head |
| Mechanics baseline now | Quality baseline only | Real models are available in CI |

## 10. Which approach fits which situation

- **A prompt or model change:** run the app on real models, `eval-compare` against the baseline,
  and read the regressions.
- **A platform change:** `just eval-baseline-check`, which CI runs.
- **"Is the writing any good?":** the rubric judge, once it has been calibrated against people.
- **A release decision (Step 38):** the published real-model results on the card.

## 11. Common mistakes

- Quoting a pass rate without the task set, its size and the model.
- Treating mock or stand-in results as quality. The tooling refuses to publish them.
- Using the judge before its agreement with people is known.
- Regenerating tasks and re-baselining to make a regression "go away". The task-set hash in every
  report shows it.
- Labelling with the judge's verdicts visible. The sheet hides them.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| Every reference app has at least 100 tasks | ✅ | 715 tasks: invoice-triage 115; hello-workflow, support-handoff, research-report, review-chat, batch-summary, policy-debate 100 each (`test_every_reference_app_has_a_current_set_of_at_least_100_tasks`, `test_the_task_set_is_current_and_large_enough`) |
| Judge agreement with human labels is measured and reported | ⏳ owner, VERIFY-ON-MAC | Measurement and reporting are built and tested (`calibrate`, Cohen's kappa matches a hand-computed example, non-human labels refused, "not measured" without labels). No human labels exist yet. People label a real-model report with `just eval-label-sheet` on the Mac. No number is claimed until then |
| A baseline is committed | ✅ mechanics; quality VERIFY-ON-MAC | `evals/baselines/*.mechanics.json` (stand-in, labelled not quality): hello-workflow 100/100, invoice-triage 85/115, batch-summary 6/100, the four other team apps 0/100 each. All 715 tasks re-ran deterministically (0 regressions). `just eval-baseline-check` runs in CI. Real-model baseline: `--provider gateway --publish` on the Mac |
| Metrics, deterministic first, results with versions | ✅ | `libs/agent-evals` tests; reports carry spec and plan hashes, models, tools, dataset SHA-256, commit; `eval-compare` names regressions |
| Harness catches bad behaviour | ✅ | An agent that obeys an injected "pay 9999" fails every injection task; a triage agent that misroutes billing fails with "delegated to tech" |
| ADR | ✅ | ADR 0045 |

## 13. Recap

Every reference app now has at least 100 golden tasks with known outcomes, scored by code from
what the platform recorded, through the real run API. Results carry their versions and can be
compared. A mechanics baseline guards deterministic behaviour in CI. The judge exists, with
written rubrics and a calibration that only people can feed. The two quality steps, real-model
runs and human labels, are set up and waiting for the Mac. Along the way the harness found and
fixed a platform defect that made team runs wait on telemetry.
