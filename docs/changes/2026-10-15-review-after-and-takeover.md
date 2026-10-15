# Review-after (sampled QA) and take-over

**Step:** 28 · **Commit:** feat(agent-api): sampled review-after and operator take-over · **Date:** 2026-10-15

## What changed
- **Review-after.** A new spec section, `review: { sample_rate, reviewers }`, sends a share of
  successful runs to a QA queue.
  - Sampling is deterministic per run id.
  - `GET /v1/reviews` lists the items: same tenant, the reviewer group, not the reviewer's own
    runs.
  - `POST /v1/reviews/{run_id}` with `{"verdict": "ok" | "issue", "note"}` records the result.
  - Both steps are audited (`review.sampled`, `review.completed`).
- **Take-over.** `POST /v1/runs/{id}/takeover` lets a person with the `operator` role, or in the
  run's approver group, take the work from the agent.
  - The agent stops, and nothing pending runs.
  - The caller receives the handover: run state, the pending action and the input.
  - They may finish with their own `answer`.
  - The run ends `taken_over`, with the answer and who gave it, and is audited as
    `run.taken_over`.
  - Operators can see the runs of their own tenant.

## Why
Step 28 also asks for the "review-after (sampled QA) and take-over patterns".

## How to check it
`uv run pytest services/agent-api/tests/test_hitl.py` (10 tests). Three are new:
- With `sample_rate` 1.0, a finished run reaches the QA reviewer, the requester cannot review
  it, and the "issue" verdict is audited.
- Sampling is deterministic, and 2,000 ids at a 10% rate land between 7% and 13%.
- An operator takes over a paused payment run: nothing is paid, the handover shows the pending
  `pay` call, and the operator's answer becomes the output.

## Risks and follow-ups
- The review queue is held in process memory. A durable queue and review metrics are planned
  for Step 30 (telemetry) and Step 34 (evals).
