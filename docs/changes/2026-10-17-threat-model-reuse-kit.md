# Threat model: the reuse kit

## What changed
- A new component in `governance/threat-model.yaml`, "Reuse kit", with four threats:
  - T57: a team's tool or pattern builder escapes the platform's controls;
  - T58: a new project runs other platform code or third-party versions than the platform
    tested and signed;
  - T59: a broken or missing team evaluator makes a failing agent look as if it passed;
  - T60: a project deploys under the platform's or another team's environment names.
- Repudiation and denial of service are marked not applicable, with the reason for each.
- A new control, C27: the reuse kit inside the platform's controls.
- `docs/security/threat-model.md` and `control-matrix.md` are regenerated.
- New tests in `libs/agent-sdk/tests/test_template.py` render the template with copier and check:
  - the pins;
  - the project's own environment names;
  - nothing left unrendered;
  - template data that is current.

## Why
Step 37's rule: revisit the threat model when components change. Step 39 lets teams' code and
repositories into the platform.

## How to check it
`just threat-check`: 60 threats, 13 components, 27 controls, 75 tests, 0 problems.

## Risks and follow-ups
A team's code is reviewed in its own repository. The platform's guarantees come from what runs
around that code, not from trusting it.
