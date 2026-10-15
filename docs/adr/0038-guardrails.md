# ADR 0038: Guardrails as a parallel pipeline around every model and tool call; rules always on, classifier and judge switchable

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 27
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 27 asks for checks before and after each model and tool call:
- prompt-injection detection on user input and on retrieved and tool content;
- PII detection and redaction (Project 0 Presidio);
- tool-argument schema and value checks;
- output schema and policy checks;
- topic and safety filters;
- secret and credential leak detection.

The checks run in parallel where possible. Alternatives: a classifier-based injection detector,
then an LLM judge or rules, each guardrail switchable.

Done when: the guardrail eval suite passes, including indirect injection via a document and via
an MCP tool description.

Constraints found in this build:
- Hugging Face is blocked from the build container, so no classifier model can be downloaded or
  measured here.
- The model server the platform would use (text-embeddings-inference) lists XLM-RoBERTa, BERT,
  ModernBERT and similar architectures, but not DeBERTa, which common open injection classifiers
  use.
- Presidio images are reachable. Presidio 2.2.362 (released 2026-03-15) was verified live: the
  `/analyze` and `/anonymize` contracts, and redaction behaviour.

## Decision
1. **One pipeline per run.** Detectors run in parallel; redactors run in order on the
   progressively redacted text. Any block blocks. Findings are audited, never the text.
2. **Where checks sit.** Model calls are checked at three points:
   - `input`, the person's latest request: a block stops the run;
   - `retrieved`, untrusted messages: a flagged message is quarantined (replaced by a notice)
     and the run continues;
   - `output`: redactions are applied, and a block stops the run.

   Tool calls are checked twice:
   - `tool_args`: a block refuses the call as denied;
   - `tool_result`: a flagged result is quarantined, and secrets and PII redacted.

   MCP tool descriptions and parameter descriptions are screened before registration; a
   poisoned tool is never offered to a model.
3. **Injection detectors.**
   - Rules always run. They cannot be switched off from a spec, and they are stricter on
     untrusted content.
   - The classifier (text-embeddings-inference `/predict` contract) joins when
     `AGENT_INJECTION_CLASSIFIER_URL` is set. It fails closed for untrusted content when it is
     unreachable.
   - The LLM judge (judge role, JSON verdict, fails closed) is chosen per spec.
   - The spec's default, "classifier", is implemented as a client. Its model deployment is
     VERIFY-ON-MAC (open issue 11).
4. **Baseline.** `prompt_injection` and `secret_leak` are added to every run, whatever the spec
   lists. Turning them off needs an ADR.
5. **PII.** Presidio is the default, with an entity list that leaves out noisy types and a score
   threshold. When Presidio is down, the check blocks. `pii_redaction.cloud_dlp` is a
   documented stub: it needs an account and sends text to a third party.
6. **Value checks from the spec.**
   - `tool_args_schema`: limits per tool argument (`max`, `min`, `enum`, `pattern`,
     `max_length`).
   - `output_schema`: a schema plus `forbidden` patterns and `max_length`.
   - `topic_filter`: blocked topics.
   - `safety_filter`: rule categories, with a support line for self-harm.
   - `secret_leak`: redacts outputs and results, and blocks tool calls that would send a
     secret.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Classifier as the only detector | Probabilistic, and unmeasured here. Rules give a deterministic floor; the classifier adds recall when deployed |
| Block the run on any flagged document | One poisoned search result would stop every run that retrieves it; quarantine removes the text and keeps working |
| LLM judge by default | Costs a model call per check and can itself be manipulated. Kept as a per-spec choice |
| Guardrails in the gateway only | The gateway sees model traffic but not tool arguments, tool results or MCP descriptions |
| Cloud DLP for PII | Needs an account and sends text to a third party; Project 0 runs Presidio locally |

## Consequences
- Streaming answers are collected before output checks, so the person sees them all at once.
- Rules have false positives and misses. Every flagged item is audited, so rules can be tuned
  from evidence.
- Australian identifiers (TFN, ABN, Medicare) need Presidio recognizer configuration
  (follow-up).

## Verification
- `just eval-guardrails` on 51 labelled cases (`evals/golden/guardrails`):
  - with the stand-in's Presidio: 51/51 pass, with 17/17 injection attacks blocked (direct 8,
    documents 6, MCP tool descriptions 3) and 23/23 benign cases allowed;
  - without Presidio: 48/48 pass, with 3 skipped.

  The detector was rules only. The dataset was written in this build and the rules were tuned
  against it once (two false positives fixed), so this shows behaviour as labelled, not a
  real-world rate.
- `services/agent-api/tests/test_guardrails_in_runs.py`, end to end through the run API:
  - a poisoned search result is quarantined and no payment is made;
  - a direct injection stops the run before any model call;
  - a payment of 9000 is refused against a limit of 500;
  - a key in the answer is redacted;
  - the poisoned MCP tool `wipe_disk` is never registered.
- The A2A test: a partner agent's hostile reply is quarantined.
- `libs/agent-guardrails/tests`: 27 unit tests plus 2 live Presidio tests.
