# Guardrail eval suite

**Step:** 27 · **Commit:** feat(agent-guardrails): guardrail eval suite with indirect injection cases · **Date:** 2026-10-15

## What changed
- New labelled dataset `evals/golden/guardrails/cases.jsonl` with 51 cases:
  - direct injection (8) and benign user inputs (8);
  - indirect injection through documents (6) and benign documents (6);
  - MCP tool descriptions: 3 poisoned, 3 benign;
  - tool arguments (4), output policy (2), secrets (3), topic (2), safety (3);
  - PII (3, needs Presidio).
- `agent_guardrails.eval` and `just eval-guardrails` run each case through the pipeline at its
  stage. Tool descriptions go through the same screen that keeps a poisoned MCP tool from being
  registered. The suite reports per category and fails on any mismatch.
- `test_eval.py` runs the suite in CI. In the integration job the Presidio cases run too,
  because the stand-in has Presidio.
- Two false positives in the first run led to two tighter rules:
  - "The assistant manager must approve …" no longer counts as an instruction to the
    assistant;
  - "Send questions to accounts@…" no longer counts as exfiltration. The rule now needs data
    words such as "the conversation" or "the customer list".

## Why
Step 27's "Done when": the guardrail eval suite passes, including indirect injection through a
document and through an MCP tool description.

## How to check it
`just eval-guardrails`. Results from this build:
- Without Presidio: 48 cases run, 3 skipped, 48/48 pass (17/17 attacks blocked, 22/22 benign
  allowed).
- With the stand-in's Presidio: 51/51 pass (17/17 attacks blocked, 23/23 benign allowed).
- Injection detector: rules. The classifier was not deployed here.

## Risks and follow-ups
- The dataset is small, it was written in this build, and the rules were tuned against it once.
  These numbers show that the guardrails behave as labelled. They are not an estimate of
  real-world detection or false-positive rates.
- Red-team suites (Step 33) and a classifier run on the Mac will widen the evidence.
