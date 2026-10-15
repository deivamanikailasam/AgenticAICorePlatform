# Guardrails: injection detectors, Presidio PII, value, output, topic, safety and secret checks

**Step:** 27 · **Commit:** feat(agent-guardrails): guardrails and the parallel guardrail pipeline · **Date:** 2026-10-15

## What changed
New package `libs/agent-guardrails`, with one registered component per guardrail.

Prompt injection:
- `prompt_injection.rules` uses patterns for:
  - overriding instructions, new roles and chat-template markers;
  - extracting the system prompt;
  - instructions addressed to the assistant, and tool coercion;
  - exfiltration (markdown images, "send it to …");
  - concealment ("don't tell the user");
  - invisible characters.

  Untrusted content (retrieved, tool results, tool descriptions) is held to a stricter standard
  than a person's own message.
- `prompt_injection.classifier` is a client for the text-embeddings-inference `/predict`
  contract (`{"inputs": text}` → label scores). It checks long text in windows. If the
  classifier is unreachable it fails closed for untrusted content.
- `prompt_injection.llm_judge` asks the judge model for a JSON verdict, with the text delimited
  as data. An unreadable verdict blocks.
- `prompt_injection`, the name specs use, runs the detectors in parallel. Rules always run; the
  classifier joins when `AGENT_INJECTION_CLASSIFIER_URL` is set.

PII:
- `pii_redaction` (Presidio) calls Project 0's analyzer and anonymizer. It applies a threshold
  and an entity list that leaves out the noisy types (PERSON, DATE_TIME, URL). If Presidio is
  down it blocks rather than letting text out unchecked.
- `pii_redaction.cloud_dlp` is a documented stub.

Other checks:
- `tool_args_schema`: JSON Schema plus per-argument `max`, `min`, `enum`, `pattern` and
  `max_length` limits from the spec.
- `output_schema`: the output schema plus `forbidden` patterns and `max_length`.
- `topic_filter`: blocked topics.
- `safety_filter`: weapons, self-harm methods (adds Lifeline 13 11 14), malware and violent
  threats.
- `secret_leak`: private keys, cloud and API keys, tokens, JWTs, connection strings and
  password assignments. Redacted in outputs and tool results; blocks tool calls that would send
  one out.

Pipeline and stand-in:
- `GuardrailPipeline` runs detectors in parallel and redactors in order. Any block blocks. It
  audits findings (`guardrail.result`), never the text.
- The stand-in gains Presidio 2.2.362 (analyzer and anonymizer, pinned by digest, released
  2026-03-15). Compose passes `P0_PRESIDIO_*` URLs to the services.

## Why
This covers Step 27's list of checks, each switchable, running in parallel where possible.

## How to check it
- `uv run pytest libs/agent-guardrails`: 27 unit tests, including the parallel timing and the
  rule that the audit never holds the text.
- With the stand-in, `-m integration`: real Presidio redacts the email and the card, keeps
  ordinary words, and passes clean text.

## Risks and follow-ups
- The injection classifier model is not deployed or measured in this build. Hugging Face is
  blocked from the build container, and text-embeddings-inference does not list DeBERTa, which
  common open injection classifiers use. VERIFY-ON-MAC; see ADR 0038.
- Australian identifiers (TFN, ABN, Medicare) need Presidio recognizer configuration. This is a
  follow-up.
