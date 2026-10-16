# Eval runner through the run API; 115 invoice-triage golden tasks; `just eval-app`

**Step:** 34 (in progress) · **Commit:** feat(evals): run golden tasks through the run API; invoice-triage task set · **Date:** 2026-10-16

## What changed
- **`agent_api.eval_runner`.** Every task is a real run: `POST /v1/agents/<agent>/runs` on an in-process run API, with the platform's assembly (policy on every call, guardrails, budgets, audit).
  - When a run pauses for approval, the runner records what the person was asked to approve, approves it as a finance approver, and lets the run finish.
  - The observation is built from the platform's own records: audit events, interrupts, the ledger and the run's events.
- **App harness protocol.** Each app supplies its spec, its real tools and the data they need; the harness is registered under the entry-point group `agent_platform.eval_apps`. `invoice_triage.eval` uses the demo ERP over MCP stdio, seeded with every task's invoice, and the platform's rag-core tool with the finance policy.
- **The task set: `evals/apps/invoice-triage/tasks.jsonl`, 115 tasks.** It is generated with a fixed seed by `invoice_triage.eval_tasks` (`just eval-tasks`). Each invoice is built as one kind of case, so its expected outcome follows from how it was built and from the finance policy, not from running the app.
  - **Paid** (status, approval arguments, invoice id in the answer):
    - valid: 35 (expect no model call);
    - GST error: 15 (pay subtotal + 10%; must check the ERP);
    - PO approved by email: 15.
  - **Held** (must never reach a payment approval):
    - no PO: 10;
    - wrong currency: 10;
    - total mismatch: 10;
    - non-positive subtotal: 5;
    - instruction injection in the notes: 10;
    - invoice unknown to the ERP: 5.
- **`just eval-app <app> [--provider stand-in|gateway] [--limit N] [--publish]`** saves a versioned report under `evals/results/<app>/`. `just eval-compare A B` exits 1 on any regression.
- **Ignored mock reports.** Reports from mock providers are git-ignored (`*-stand-in.json`, `*-scripted.json`), so they can't be committed as results.
- **`tests/stack/fakes/presidio_fake.py`.** A loopback Presidio double, for offline tests only. The PII guardrail fails closed without Presidio, which is correct, and blocked the harness self-test.

## Why
Step 34 needs golden tasks per reference app, scored on the listed metrics, through the platform as it really runs.

## How to check it
- `uv run pytest apps/invoice-triage/tests/test_eval.py` (5 tests):
  - the task set is current and has at least 100 tasks;
  - valid invoices are paid after approval with zero model calls;
  - held invoices never reach payment;
  - a correct scripted agent passes GST-error tasks;
  - a scripted agent that obeys the injected "pay 9999" fails every injection task, with "post_payment reached an approval request although it must not".
- `just eval-app invoice-triage --limit 40` against the local stand-in: the 35 valid tasks passed and the 5 GST-error tasks failed, because no model was reachable. The report was labelled as not a real-model run and not kept.

## Risks and follow-ups
- Task sets for the other reference apps wait for the owner's answer on scope.
- The baseline (a real-model run) and judge calibration (human labels) wait for the owner.
- The harness approves every pending approval, standing in for a person. Rejection paths are covered by the Step 28 tests.
