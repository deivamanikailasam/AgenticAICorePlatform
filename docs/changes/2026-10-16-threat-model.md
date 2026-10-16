# Threat model and control matrix, checked on every commit

**Step:** 37 (in progress) · **Commit:** feat(governance): STRIDE threat model and control matrix with enforced owners, controls and tests · **Date:** 2026-10-16

## What changed
- **`governance/threat-model.yaml`.** 49 STRIDE threats across 11 components: run API, runtime, tool layer, policy, gateways, sandbox, memory, run store and checkpoints, audit, governance and supply chain, telemetry.
  - Each component has its trust boundary and data.
  - Each threat has an owner role, the controls that answer it, the OWASP agentic risks (ASI01–ASI10) it maps to, and its automated tests (pytest nodes or red-team ids).
  - Where a STRIDE category doesn't apply to a component, the file says why.
  - Owner roles (platform, security, privacy, finance-platform) each name a person; today that is the single maintainer.
- **`governance/controls.yaml`.** 22 controls. Each links to evidence in the repository (code, policies, ADRs, tests) and maps to NIST AI RMF (functions), ISO/IEC 42001 Annex A (objectives), EU AI Act articles, the Australian Privacy Principles and the AU Guidance for AI Adoption practices.
- **`just threat-check`, now in `just check`.** It enforces:
  - every threat has an owner who is a person or team, at least one known control, and at least one test;
  - every listed test is collected by pytest or is a known red-team id;
  - every STRIDE category is covered or explained;
  - every control has evidence that exists, including at least one test, and answers at least one threat;
  - the generated pages are current.
- **Generated pages** (`just threat-model`): `docs/security/threat-model.md` and `docs/security/control-matrix.md`.
- **Hand-written pages:**
  - `docs/security/data-flows.md`: a data-flow diagram with trust boundaries, and a table of data by kind (where stored, protection, where it goes, lifetime);
  - `docs/security/compliance-sources.md`: each framework's source and status as checked today, including caveats.

## Why
Spec Step 37's "Done when" asks that every threat has an owner, a control and a test, and that the control matrix links to evidence in the repo. A YAML register that CI checks keeps those links true as the code moves. A document can't.

## How to check it
- `just threat-check`: 49 threats, 11 components, 22 controls, 52 tests, 0 problems. All ten ASI categories appear in the mapping.
- `uv run pytest libs/agent-governance/tests/test_threats.py`: a complete model passes. A broken one reports each violation by name: bad STRIDE letter, unnamed owner, unknown control, unknown red-team test, duplicate id, missing owner, control or test, uncovered category, a category both covered and excused, a control with no test, missing evidence, an unknown framework, an unused control.

## Risks and follow-ups
- The framework mapping is coarse on purpose: NIST functions and ISO objectives, not subcategory or control numbers. The compliance review should refine it.
- When EU high-risk obligations start is unsettled (2 August 2026 in the original text; a reported deferral to December 2027 for some systems is not confirmed against an official source).
- Australia's automated-decision transparency duties (APP 1.7–1.9) start on 10 December 2026.
