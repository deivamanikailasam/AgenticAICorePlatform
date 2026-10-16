# Eval runs record model calls, tokens and cost; streamed calls are costed

**Step:** 36 (found while building the eval gates) · **Commit:** fix(evals): record usage in eval runs and cost streamed calls · **Date:** 2026-10-16

## What changed
- **Eval runs record model usage.** The eval runner's gateway client kept its own private ledger, so every eval report showed 0 model calls, 0 tokens and AUD 0. That included real-model runs, so the Step 34 checks `max_model_calls`, `max_tokens` and `max_cost_aud` could never fail. The session now gives gateway clients the assembly's ledger.
- **Team runs count every worker.** Workers record usage under their own run (`<run>/<worker>-<n>`). The new `UsageLedger.tree(run_id)` totals a run and everything under it, and observations use it.
- **Streamed calls are costed.** LiteLLM can't send a stream's cost in the response headers, because it is only known at the end. It sends it in the final usage chunk (`usage.cost`), and the gateway client now reads it from there. Until now every streamed call was recorded at AUD 0, including the cost spans, the ledger and budget roll-ups.

## Why
The Step 36 gates compare cost against the baseline and against a ceiling. Those comparisons mean nothing while recorded usage is 0.

## How to check it
- `uv run pytest libs/agent-runtime/tests/test_gateway.py`: a new test streams a call that reports USD 0.002 in its usage chunk; the ledger records it, and AUD 0.003 at the platform's rate.
- Against the stand-in, the same tasks before and after the fix:
  - invoice-triage GST-error tasks: 0 model calls before; now 2 model calls and 60 tokens each (AUD 0, because the planner's stand-in model has no price);
  - support-handoff tasks: now 2 model calls, 258 to 260 tokens, AUD 0.00038 each.
- Two runs of the same 40 tasks for each of three apps gave identical tokens per task.

## Risks and follow-ups
- Eval reports made before this change under-report usage. The committed mechanics baselines are re-recorded in the next commit.
