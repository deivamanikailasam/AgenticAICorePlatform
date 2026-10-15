# LiteLLM team budgets, a persisted cost ledger, and `just cost-reconcile`

**Step:** 26 · **Commit:** feat(cost): LiteLLM team budgets, persisted usage ledger and cost reconciliation · **Date:** 2026-10-15

## What changed
- **Hierarchical budgets on the gateway.** `just provision-litellm` creates one LiteLLM team,
  `agent-platform`, and puts every role key in it (`team_id`). The team's `max_budget` is the
  sum of the role caps (or `team.max_budget_usd`), so the team caps the platform and each key
  caps its role. Per-run and per-agent limits stay in the platform's budget controller.
- **Persisted ledger.** `PostgresUsageLedger` writes every model call to
  `agent_runtime.usage_ledger` (migration 0006). Each row holds tokens, cost in AUD, the cost in
  USD exactly as LiteLLM reported it, the response id, and `key_name`: whose key paid, which can
  be a fallback key. The API and the worker use it when `AGENT_DB_DSN` is set; the embedder too.
- **`just cost-reconcile`** compares the ledger with LiteLLM's spend logs for each
  `agent-platform-<role>` key over UTC dates. `--wait` allows for LiteLLM's batched writes. It
  exits non-zero on a mismatch.
- The stand-in's `local-qwen-fast` now has prices (USD 0.000001 per input token, 0.000002 per
  output token), so cost accounting is exercised in CI. These are stand-in values, not real
  prices.

## Why
Step 26 asks for "hierarchical budgets mapped to LiteLLM virtual keys", and its "Done when"
requires that cost dashboards match the gateway's accounting. The match is now checked by a
command and by an integration test.

## How to check it
- `uv run pytest tests/scripts/test_cost_reconcile.py tests/scripts/test_provision.py libs/agent-runtime/tests/test_ledger.py`
- With the stand-in (`-m integration`): 3 calls through a new key were recorded by the platform
  as USD 0.00015 and reported by LiteLLM as USD 0.00015.
- Every LiteLLM call used here was checked against the running stand-in (LiteLLM v1.105.1):
  `/team/new`, `/team/list`, `/key/generate` with `team_id`, `/key/info`, `/team/info` and
  `/spend/logs`. That check found that `/spend/logs` treats `end_date` as exclusive, and the
  query allows for it.

## Risks and follow-ups
- Keys created before this change are not moved into the team. Recreate them, or move them in
  LiteLLM's UI.
- LiteLLM writes spend in batches (about a minute in the stand-in), so a reconciliation run
  straight after traffic needs `--wait`.
- VERIFY-ON-MAC: run against Project 0's LiteLLM with real prices.
