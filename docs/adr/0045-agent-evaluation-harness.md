# ADR 0045: Agent evaluation: deterministic checks over platform records, generated golden tasks, an honest baseline, and a judge calibrated only by people

- **Status:** Accepted
- **Date:** 2026-10-16
- **Step:** 34
- **Deciders:** repository owner (delegated the open decisions, 2026-10-16)

## Context
Step 34 asks for:
- golden task sets per reference app with expected outcomes;
- metrics: task success, final-answer correctness, trajectory quality, tool-call accuracy,
  policy compliance, steps, tokens, AUD cost, latency;
- deterministic checkers first, and an LLM judge only with written rubrics and calibration
  against human labels;
- results stored with versions for comparison.

Done when:
- every reference app has at least 100 tasks;
- judge agreement with human labels is measured and reported;
- a baseline is committed.

This build has no real models, only the stand-in gateway's canned responses (the local Qwen
models are on the Mac). It has no human labellers. Two rules of the build apply directly: no
invented numbers, and no invented data.

## Decision
1. **Score what the platform recorded, not what the model said.** Checks read the audit trail
   (tool calls with arguments, policy decisions, delegations, guardrail results), the approval
   requests, the ledger and the run's events. Every metric in the spec has a deterministic
   check; a task scores only the checks it names.
2. **Tasks are generated and built as known cases.** Seeded generators write the sets (715
   tasks across the 7 reference apps). Each expected outcome follows from how the case was
   built (an invoice with a GST error, a billing question, two documents with known figures),
   never from running the app.
3. **Reference apps are the 7 top-level specs** (owner's delegated decision), the same set that
   has system cards (Step 32).
4. **Runs go through the run API**, with the platform's assembly and each app's real tools. An
   approval is given by a finance approver, after recording what was asked.
5. **The judge uses written rubrics only**, at temperature 0, with its answer constrained to the
   rubric's labels. Calibration (agreement and Cohen's kappa) accepts only labels from a named
   human labeller. Without them it reports "not measured", never a number.
6. **Baselines are honest** (owner's delegated decision).
   - A *mechanics* baseline from the stand-in is committed and checked in CI. It pins
     deterministic behaviour and is labelled as not a quality measure.
   - The real-model baseline is run and published on the Mac.
   - Mock runs can't be published to agent cards, can't be labelled, and their reports are
     git-ignored.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Hand-written tasks | 700 tasks by hand would be slow and inconsistent; generators make every expected outcome traceable to its construction and the sets reproducible |
| Model-generated tasks and answers | The expected answers would come from the kind of system under test |
| Scoring the model's own account (final text only) | Misses what actually ran; the audit trail is the ground truth for tools, policy and delegation |
| Judge-only scoring | Unverifiable without calibration; most of these outcomes are decidable by code |
| Generating "human" labels with a model | They are not human labels; calibrating a model against a model measures nothing |
| No baseline until the Mac | Leaves deterministic regressions uncaught now; the mechanics baseline catches them in CI |
| OpenAI Evals, Promptfoo, DeepEval, Ragas | Good for prompt-level checks; none reads this platform's audit trail, approvals or delegations. Their metrics can be added as evaluators later |

## Consequences
- **The judge-agreement check is not met here.** It needs people to label a real-model report
  (`just eval-label-sheet`, then `calibrate`). This is the owner's action, on the Mac.
- **Real-model baseline (VERIFY-ON-MAC):** `just eval-app <app> --provider gateway --publish`
  for each app. Cards then cite it.
- **Mechanics baseline numbers** (stand-in, not quality): hello-workflow 100/100 (no model
  involved, so a real result); invoice-triage 85/115; batch-summary 6/100; the four other team
  apps 0/100 each.
- **CI cost:** the baseline check adds about 3 minutes to the integration job.
- **A platform defect the harness found:** every team worker flushed telemetry synchronously.
  It is fixed.
