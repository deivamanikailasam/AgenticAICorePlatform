# ADR 0047: CI eval gates per risk tier: regressions on every pull request, quality ceilings on real runs, explicit approval for cost

- **Status:** Accepted
- **Date:** 2026-10-16
- **Step:** 36
- **Deciders:** repository owner (delegated the open decisions, 2026-10-16)

## Context
Step 36 asks for `evals/gates.yaml` per risk tier: minimum task success, maximum regression
versus the baseline, zero critical red-team failures, and cost and latency ceilings. The gates
run on every pull request that touches specs, prompts, tools, models or patterns, full suites
run nightly, and results are posted to the pull request.

Done when:
- a pull request that lowers task success beyond the threshold is blocked;
- a cost increase above the ceiling needs explicit approval.

This build's CI has only the stand-in gateway: canned answers, made-up prices, timings that
mean nothing. Building the gates also showed that eval runs recorded no usage at all (fixed in
c8c8e39).

## Decision
1. **One file, three tiers, named overrides.** `evals/gates.yaml` sets thresholds per risk tier,
   and a spec's `evals.gates` can pick a stricter named set. The numbers are owner policy, not
   measurements. Higher tiers are stricter, and only the high tier needs sign-off.
2. **Checks apply where they mean something.**
   - On every run, including the stand-in in CI, the gate compares against the baseline of the
     same kind:
     - tasks that newly fail;
     - the drop in success rate;
     - red-team findings by severity;
     - growth in tokens and AUD per task.

     On the stand-in, tokens are deterministic (re-checked: identical per task across runs for
     three apps; batch-summary moves by about 1%).
   - Only on real-model runs: the absolute success floor, the AUD-per-task ceiling and latency.
     On a stand-in run these checks say "n/a" with the reason, rather than passing silently.
3. **Blocked or approved.**
   - A regression or a red-team finding over the tier's limit blocks the merge. So does a
     changed task set without a re-recorded baseline.
   - Cost growth or an AUD ceiling breach needs explicit approval: an APPROVED review on the
     head commit, by an approver for the tier (`governance/approvers.yaml`), not the author,
     containing `/approve-cost`. High-tier apps also need `/eval-signoff`.
   - An ordinary approval doesn't count. Neither does an approval on an earlier commit.
4. **Where it runs.**
   - In CI's integration job, which already has the stack, after the red-team suite. A pull
     request is judged on the apps it touches, inferred from the files it changes: shared code,
     `platform.yaml` with its models, policies and the gates file touch every app. The results
     are posted as one comment, updated in place.
   - Nightly, and before every release, in a single full job reused by both
     (`release.yml`, `nightly.yml`).
5. **Baselines record usage.** Mechanics baselines now keep tokens and AUD. Real-model baselines
   (`<app>.quality.json`) are made on the Mac.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Absolute success floors on stand-in runs | The stand-in's canned answers make most team apps fail by design; a floor would either block everything or be set to 0 and mean nothing |
| A label for cost approval | Labels aren't tied to a commit, and checking who added one needs another API; a review on the head commit is explicit, attributable and invalidated by new pushes |
| Any approving review approves cost | Makes the approval implicit; the spec asks for explicit approval of a cost increase |
| A separate eval-gates workflow | Would need its own stack bring-up (about 3 minutes); the integration job already has it |
| Running only affected apps nightly | Shared-code drift goes unseen; nightly runs everything |
| Promptfoo/DeepEval threshold assertions | Judge single prompts; these gates read the platform's runs, baselines and red-team report |

## Consequences
- **Evidence.**
  - Unit and end-to-end tests: a misrouting team change (success 100% to 33.3% on 6 tasks) is
    blocked; a change at twice the price per call (+100% AUD per task) waits for approval and
    passes once approved.
  - Live on the stand-in: removing invoice-triage's GST check made `just eval-gate` block the
    change (5 new failures against a limit of 2; −4.3 points against a limit of −2).
  - All 7 apps passed against the re-recorded baselines.
- **VERIFY-ON-MAC:** real-model runs and `<app>.quality.json` baselines, so the success floors,
  AUD ceilings and latency apply.
- **Owner:** with a single maintainer, nobody can approve their own pull request's cost increase
  (GitHub refuses self-approval). Adding a second approver, already an owner action from Step
  32, unblocks this too.
- **CI cost:** the gate replaces the Step 34 baseline check, which took about 2 minutes, at about
  the same time.
