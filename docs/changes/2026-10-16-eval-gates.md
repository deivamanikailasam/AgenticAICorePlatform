# Eval gates per risk tier, judged against baselines that now record usage

**Step:** 36 (in progress) · **Commit:** feat(evals): eval gates per risk tier with regression, red-team and cost checks · **Date:** 2026-10-16

## What changed
- **`evals/gates.yaml`.** Thresholds for each risk tier (low, medium, high), plus named sets a spec can pick in `evals.gates`:
  - task success: a minimum rate, the largest allowed drop in percentage points versus the baseline, and the most tasks that may newly fail;
  - red-team findings allowed, by severity (required from medium up);
  - cost: growth in AUD and tokens per task, and an AUD-per-task ceiling;
  - latency: a p95 ceiling and the largest allowed growth;
  - sign-off for the high tier.

  These numbers are policy set by the owner's delegation, not measurements.
- **`agent_evals.gates`.** Each check comes out pass, fail, needs approval, approved, or not applicable with a reason. The verdict is blocked, needs approval, or pass.
  - Stand-in runs are judged against the mechanics baseline: regressions, red team, token and cost growth.
  - Absolute success, the AUD ceiling and latency apply only to real-model runs. A changed task set needs a re-recorded baseline in the same change.
- **`scripts/eval_gate.py` and `just eval-gate`.**
  - It picks the apps a change touches: specs, app code and tasks touch that app; shared code, `platform.yaml` (models), policies and the gates file touch every app; docs touch none.
  - It runs each app through the run API and judges it against its baseline and red-team report.
  - It reads explicit approvals from pull-request reviews: an APPROVED review on the head commit, by an approver for the tier who isn't the author, containing `/approve-cost` or `/eval-signoff`.
  - It writes `evals/results/gates/` and can post one updated comment on the pull request.
- **Mechanics baselines re-recorded with usage** (tokens and AUD, overall and tokens per task). Success counts are unchanged: hello-workflow 100/100, invoice-triage 85/115, batch-summary 6/100, the four other team apps 0/100.
- **Scripted models' usage is recorded in eval sessions**, so tests can exercise the cost gate.

## Why
Spec Step 36 calls for per-tier gates with a minimum success rate, a maximum regression, zero critical red-team failures, and cost and latency ceilings.

Done when:
- a change that lowers task success beyond the threshold is blocked;
- a cost increase above the ceiling needs explicit approval.

## How to check it
- `uv run pytest libs/agent-evals/tests/test_gates.py`: 8 tests. 2 new failures pass and 3 are blocked on the medium tier. Cost +20% passes, +30% needs approval, and approval passes it. Red-team findings block by severity. Quality-run floors and ceilings apply. A changed task set blocks, and high-tier sign-off is required.
- `uv run pytest services/agent-api/tests/test_eval_gates.py`: through the run API with scripted support agents.
  - A triage that misroutes billing drops success from 100% to 33.3% on 6 tasks, and the change is blocked.
  - A model at twice the price per call gives +100% AUD per task, which needs approval and passes once approved.
  - Affected-app mapping and review-based approvals are covered too.
- `just eval-gate --redteam <report>` locally on the stand-in passed all 7 apps against the new baselines. batch-summary's tokens per task varied by +1.2% from the baseline, within the 25% limit.

## Risks and follow-ups
- Stand-in prices are made up, so cost growth on the stand-in shows relative change, not real spend. AUD ceilings and latency apply on the Mac with real models (VERIFY-ON-MAC).
- Tokens on batch-summary vary slightly between runs (map-reduce ordering), so its token limit is a band, not an exact match.
