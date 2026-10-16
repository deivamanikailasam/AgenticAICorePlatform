# Attack libraries: promptfoo and garak against the run API

**Step:** 35 (in progress) · **Commit:** feat(redteam): promptfoo and garak attack libraries against the run API · **Date:** 2026-10-16

## What changed
- **Target adapter (`evals/redteam/libraries/target.py`).** Standard library only. Each attack prompt becomes one real run of a real agent through the run API: Keycloak token, OPA, guardrails, approvals, audit. The reply is the run's output. When the platform stopped a run, the reply describes it instead, e.g. `[approval requested: post_payment {...}]` or `[run stopped: ...]`.
- **promptfoo 0.123.1** (released 2026-09-18, outside the 14-day cooldown).
  - `promptfooconfig.yaml` is a fixed library of 10 canary-token attacks with deterministic assertions:
    - direct and indirect injection, a role-play jailbreak, base64, markdown-image exfiltration (ASI01);
    - a fake tool result (ASI07);
    - a persistent instruction (ASI06);
    - a privilege claim (ASI03);
    - template injection (ASI05);
    - approval pressure (ASI09).
  - `promptfooconfig.redteam.yaml` generates attacks with promptfoo's own `owasp:agentic` framework (ASI01 to ASI10, checked in the 0.123.1 package). The local model generates and grades them through the gateway (VERIFY-ON-MAC).
  - Remote generation, sharing, telemetry and update checks are always switched off.
- **garak 0.17.0** (released 2026-09-09).
  - It runs from its own environment, because it brings torch: `just redteam-garak-install` installs CPU wheels only.
  - Its 9 probes have string or pattern detectors, so no detector models are downloaded: promptinject (2), latentinjection (2), web_injection markdown exfiltration, encoding (base64, ROT13), and exploitation (Jinja, SQL).
  - `mapping.yaml` maps each probe family to an ASI category and a severity.
- **Import into findings (`agent_evals.redteam_libraries`).**
  - promptfoo cases and garak probes become red-team results (sources `promptfoo` and `garak`), so they share the report, issue tracking and the release gate.
  - Fail-closed: a promptfoo case that could not run, or a configured garak probe that left no result, is a finding.
- **Commands:** `just redteam-promptfoo`, `just redteam-promptfoo-generate`, `just redteam-garak-install`, `just redteam-garak`.
- **New workflow `redteam.yml`.** It runs both libraries on the stand-in stack weekly, on demand, and when red-team files change.

## Why
Spec Step 35 says promptfoo and garak run the attack libraries. The suite's scenario attacks assume the worst model, to test the controls. The libraries add many known attack prompts against the real model, to measure how often attacks get through the model and the guardrails.

## How to check it
Run in this container against the stand-in agent-api on 127.0.0.1:8091:
- promptfoo: 10 attacks executed, 10 held.
- garak: 9 probes, 9 held. Each probe's attempts were evaluated by garak's own detectors; the counts are in `evals/results/redteam/garak.json`.

Not measured here: the local stand-in had no gateway key for the support team's model role, so attacks ended at the guardrail or as a stopped run. These results check the attack path, not model behaviour. CI's `redteam` workflow provisions the stack fully. Real-model susceptibility is VERIFY-ON-MAC (`AGENT_REDTEAM_PROVIDER=gateway`).

## Risks and follow-ups
- `npx` downloads promptfoo on each CI run (about 2 GB installed). Caching it is a possible follow-up.
- garak's HijackLongPrompt probe ignores the prompt cap (256 runs), so it is left out to keep runs bounded.
