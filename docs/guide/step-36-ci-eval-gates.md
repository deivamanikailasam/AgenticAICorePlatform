# Step 36 · CI eval gates

## 1. At a glance

| | |
|---|---|
| **Goal** | No change to an agent merges unless its evals say it's no worse: fewer tasks right, new red-team findings and quiet cost growth are caught before merge |
| **Time** | About a day |
| **You end up with** | `evals/gates.yaml` (thresholds per risk tier); `agent_evals.gates` (the checks and verdict); `just eval-gate` (runs the affected apps and judges them); results posted to the pull request; nightly full suites; the eval gate inside the release gate; two accounting fixes (eval usage, streamed-call cost) |
| **New words** | eval gate, regression, threshold, risk tier gate, mechanics vs quality run, explicit approval, affected app |

## 2. The big idea

Step 34 measures how well each agent does. Step 36 turns those measurements into a rule:
a change merges only if it's no worse than what's already there, by amounts the owner has
decided. The stricter the agent's risk tier, the less slack it gets.

> *Analogy:* a building inspection before a renovation is signed off. The inspector has a
> checklist per building type (a shed, a house, a hospital), compares against the approved plan,
> and some things, like a bigger budget, aren't failures but need the owner's signature.

## 3. What we're doing

1. Write the thresholds per risk tier in `evals/gates.yaml`.
2. Judge each run against its baseline, the red-team report and those thresholds. The result is
   blocked, needs approval, or pass.
3. Work out which apps a pull request touches, and run only those (all apps on a push or at
   night).
4. Read explicit approvals from the pull request's reviews.
5. Post the results to the pull request, and run everything nightly and before a release.

## 4. Why we're doing this

Agents get worse quietly. A prompt tweak that helps one case breaks three others. A model swap
doubles the cost. A new tool opens a hole. People review code, not hundreds of runs, so the
check has to be automatic, and it has to be strict where the risk is high.

> *Watch out:* a gate is only as good as what it measures. While building this step we found that
> eval runs had recorded no usage at all, and that streamed calls were costed at AUD 0. Both are
> fixed. A cost gate on zeros would have passed everything.

## 5. Key ideas

- **Risk tier gates.** These are owner policy, not measurements:
  - low: at least 80% success on real runs, at most 5 points' drop and 5 new failures, approval
    above +25% cost;
  - medium: 90%, 2 points, 2 failures, +15% cost, and no critical or high red-team findings;
  - high: 95%, no drop at all, +10% cost, no medium-or-worse findings, plus a named sign-off.
- **Mechanics vs quality runs.**
  - A *mechanics* run uses the stand-in. It can show regressions, red-team results and usage
    growth, but not quality, real cost or latency.
  - A *quality* run uses the real model. The absolute floors and ceilings apply only to these.
  - Checks that don't apply say "n/a" and why. They never pass silently.
- **Regression.** A task that passed in the baseline and fails now. The gate counts them by
  name.
- **Explicit approval.** An approving review on the head commit, from an approver for that
  tier, not the author, saying `/approve-cost` (or `/eval-signoff`). A plain "LGTM" doesn't
  approve a cost increase.
- **Affected apps.** What a pull request touches, worked out from its files:
  - a spec, app code or tasks touch that app;
  - shared libraries, `platform.yaml` (models), policies and the gates file touch every app;
  - docs touch none.

## 6. How to do it

```bash
just eval-gate --redteam evals/results/redteam/integration.json   # every app, stand-in
just eval-gate invoice-triage --approve cost                       # one app, try an approval locally
just eval-gate --base origin/main --github --comment               # what CI runs on a pull request
just eval-baseline                                                 # re-record mechanics baselines after an intended change
# on the Mac, with real models
just eval-gate --provider gateway --redteam evals/results/redteam/suite.json
```

To make an app stricter, add a named set under `named:` in `evals/gates.yaml` and set
`evals: { gates: <name> }` in its spec.

## 7. Benefits

- **A worse change can't merge silently.** The comment names the tasks that broke.
- **Cost growth is visible and needs a person's name on it.**
- **Red-team findings and quality thresholds sit in one verdict per app.**
- **One file, reviewed like code.** Changes to gates go through review too: `.github/` and
  `evals/` changes are governed.

## 8. Drawbacks and risks

- **Stand-in numbers are about mechanics.** Real quality, AUD and latency gating happens with
  real models on the Mac.
- **Thresholds are judgement calls.** Too loose and they catch nothing; too tight and people
  stop trusting them. Review them as results arrive.
- **A single maintainer can't approve their own cost increase** (GitHub refuses self-approval).
  Add a second approver.
- **Slight run-to-run variance** (batch-summary tokens move by about 1%) means usage limits are
  bands, not exact matches.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Baseline-relative checks on every PR | Absolute floors on every PR | When CI has real models |
| Review-based approval | A label | When approvals don't need to be tied to a commit |
| Gate inside the integration job | A separate workflow | When the stack is cheap to start, or gates run elsewhere |
| Affected apps only on PRs | Every app on every PR | Small repositories where the full run is fast |

## 10. Which approach fits which situation

- **A prompt or spec change to one app:** the PR gates that app, and the comment shows the
  result.
- **A shared library or model change:** every app is gated.
- **A deliberate trade (more cost for better results):** re-record the baseline if needed, and
  get `/approve-cost` from an approver.
- **Before a release:** `release.yml` runs every gate and the full red team.

## 11. Common mistakes

- **Re-recording the baseline to make a regression disappear.** The baseline diff is in the PR
  for reviewers to see.
- **Treating stand-in cost as real spend.** It shows relative change only.
- **Approving with "LGTM" and expecting the cost check to clear.** Write `/approve-cost`.
- **Gating on a metric that isn't being recorded.** Check the numbers move before trusting a
  "pass".

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| A PR that lowers task success beyond the threshold is blocked | ✅ | Test through the run API: a misrouting triage drops success from 100% to 33.3% on 6 tasks, and the change is blocked. Live on the stand-in: removing invoice-triage's GST check made `just eval-gate` block it (5 new failures against a limit of 2; −4.3 points against a limit of −2) and exit 1. In CI the job fails, so the PR can't merge once the check is required |
| A cost increase above the ceiling needs explicit approval | ✅ | A model at twice the price per call gives +100% AUD per task: **needs_approval**, and passes with `cost` approval. A review counts only if it's APPROVED, on the head commit, from a tier approver who isn't the author, and contains `/approve-cost`. Tested: author, non-approver, earlier commit, commented-only and plain "LGTM" are all refused |
| `evals/gates.yaml` per risk tier (success, regression, red team, cost, latency) | ✅ | Three tiers, stricter upwards, with named overrides. Each check is applied or reported as n/a with the reason |
| Runs on PRs touching specs, prompts, tools, models or patterns; nightly full suites; results posted to the PR | ✅ (live PR comment on the first PR) | ci.yml gates the affected apps on PRs and every app on pushes; nightly.yml runs the full release job; the comment is created or updated by `--comment` |
| ADR | ✅ | ADR 0047 |

## 13. Recap

Every agent change now meets a gate set by its risk tier. Broken tasks and red-team findings
block it. Cost growth waits for a named approver. On the Mac, real quality, AUD and latency
ceilings apply. The gate runs on pull requests, nightly and before releases, and posts what it
found. Building it also uncovered and fixed two accounting gaps that would have made any cost
gate meaningless.
