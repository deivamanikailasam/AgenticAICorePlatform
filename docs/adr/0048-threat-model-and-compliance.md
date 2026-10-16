# ADR 0048: Threat model and compliance as checked data: STRIDE per component, a control matrix with evidence, and a data lifecycle with export and retention

- **Status:** Accepted
- **Date:** 2026-10-16
- **Step:** 37
- **Deciders:** repository owner (delegated the open decisions, 2026-10-16)

## Context
Step 37 asks for:
- a STRIDE threat model per component, plus the OWASP agentic mapping;
- data-flow diagrams;
- a control mapping to NIST AI RMF, ISO/IEC 42001, the EU AI Act and the Australian Privacy
  Principles;
- a data lifecycle for runs, memory and transcripts (retention, erasure, export).

Done when every threat has an owner, a control and a test, and the control matrix links to
evidence in the repo.

Erasure already existed (Step 20). Export of a person's data didn't, and neither did retention
for runs and transcripts. Several framework details are moving: when EU high-risk obligations
start, and Australia's automated-decision transparency duties from 10 December 2026.

## Decision
1. **The threat model is data, checked in CI.**
   - `governance/threat-model.yaml` (components, STRIDE threats, owners, controls, OWASP
     mapping, tests) and `governance/controls.yaml` (controls, evidence, framework mapping) are
     the source.
   - `just threat-check` in `just check` enforces the "Done when" mechanically: owners name a
     person or team; controls exist; tests are collected or are known red-team ids; evidence
     paths exist; every STRIDE category is covered or explained; no control is unused.
   - The pages in `docs/security/` are generated from these files and checked for staleness.
2. **Tests are reused, not duplicated.** Threats point at existing tests and at the Step 35
   red-team register, which already proves most controls.
3. **Framework mapping at the level the sources support.** NIST functions; ISO 42001 Annex A
   objectives; EU AI Act articles; APP numbers; the six Guidance for AI Adoption practices.
   Sources and caveats are in `docs/security/compliance-sources.md`, dated. No subcategory or
   control numbers that weren't verified.
4. **Data lifecycle in code, with owner-set values.**
   - `governance/data-lifecycle.yaml` sets retention:
     - finished runs and transcripts: 90 days;
     - checkpoints: 30 days, keeping each thread's newest;
     - memory: per kind;
     - audit: about 7 years, append-only, holding no content.
   - `POST /v1/admin/retention` applies it. It never prunes a run that is still going or
     waiting for a person.
   - `GET /v1/me/export` and `POST /v1/admin/export` give a person their data (APP 12).
   - All of it is audited.
5. **Owners are roles bound to people.** Eight roles, each with a stated responsibility:
   security architect, AI safety lead, privacy officer, SRE lead, platform lead, release
   manager, audit and compliance lead, finance controls owner. All are held today by the single
   maintainer (owner decision, 2026-10-16) and are split as people join.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| A threat-model document only (or a tool like Threat Dragon / pytm) | Links rot silently; nothing fails when a test is renamed or a control removed. pytm generates diagrams from code and would suit a larger team; the checked YAML keeps the "Done when" true on every commit |
| One threat per OWASP category only | Misses non-agentic threats (database dumps, audit tampering, restarts); STRIDE per component catches them |
| Fine-grained clause mapping (NIST subcategories, ISO control numbers) | Can't be verified here without the standards; an unverified number is worse than a coarse true one |
| Retention as a database job | Bypasses the platform's rules (never prune a waiting run) and isn't audited; the API applies the same rules everywhere |
| Export only by a privacy officer | APP 12 is the person's right; self-service with audit is simpler and reduces handling of personal data by staff |

## Consequences
- **Evidence.**
  - `just threat-check`: 49 threats, 11 components, 22 controls, 52 tests, 0 problems.
  - All ten ASI categories are mapped.
  - Lifecycle tests: export (own data only; another user and another tenant refused; officer
    allowed; audited) and retention (old finished run pruned, waiting run kept; audited).
- **Owner and compliance review.**
  - Confirm the retention values.
  - Refine the framework mapping.
  - Classify each agent under the EU AI Act if used in the EU.
  - Write the APP 1.7 privacy-policy text for deployments where agents support decisions
    about people.
  - Add people to the owner roles.
- **Step 38** schedules the retention call and adds HA and backup controls to the matrix.
