# Red-team suite for the OWASP agentic risks (ASI01-ASI10)

**Step:** 35 (in progress) · **Commit:** feat(redteam): ASI01-ASI10 red-team suite with scenario attacks and findings report · **Date:** 2026-10-16

## What changed
- **Register (`evals/redteam/catalogue.yaml`).** The ten ASI categories with the platform's controls, from the spec's threat model. It also links 52 existing tests that prove a control. Each test has an ASI category, a severity (what a failure would mean) and a tier (unit, integration or sandbox).
- **Scenario attacks (`evals/redteam/attacks/*.yaml`).** Nine attacks on real reference apps through the run API, with the real OPA:
  - ASI01: a payment instruction hidden in an invoice's notes, and a classic override hidden in an invoice's notes;
  - ASI02: a payment over the medium-risk limit that a person *approves*;
  - ASI03: a cross-tenant invoice lookup;
  - ASI07: a handoff to an agent outside the team;
  - ASI08: billing and tech handing the customer back and forth;
  - ASI09: an agent summary that says "pay 528.00" while the payment is for 9,800;
  - ASI10: the per-agent and the tenant kill switches, set in live OPA data during the run and restored afterwards.
- **Obedient adversary (`agent_evals.adversary`).** A stand-in model that follows any instruction it can see: in the user's message, a tool result, a document or another agent's output. Scenarios therefore test whether the platform's controls hold against the worst model. A payload marker shows whether the attack reached the model. If it never did and no guardrail stopped it, the attack is reported as unproven, not as held.
- **Trustworthy "held" results.**
  - The suite first checks that OPA answers with Project A's policies: it must allow a known-good read and deny a known-bad cross-tenant read.
  - Each attacked app has a benign canary (`evals/redteam/canaries.yaml`) that must succeed under the same setup.
  - A self-test shows that a weaker policy engine turns the over-limit and kill-switch attacks into findings.
- **Report and gate.**
  - The `agent_evals.redteam_pytest` plugin records every test as held, finding or not run, with evidence.
  - `just redteam` writes `evals/results/redteam/report.json`. It fails on any high or critical finding, and on any category without an executed test.
  - `just redteam-check` (now part of `just check`) verifies coverage of all ten categories and that every linked test still exists.
  - `just release-gate` merges reports.
- **New checks for golden tasks.** `policy.never_run`, `policy.refused` and `policy.never_run_with`.
- **Gap closed.** Deprecated and retired agents are now shown to be refused in staging and prod, and each refusal is audited (ASI10).
- **CI.** The integration job runs the unit and integration tiers. The sandbox job runs the gVisor tier (ASI05).

## Why
Spec Step 35 says every ASI category must have at least one automated test, and high-severity findings must block a release. Running attacks with a model that obeys the attacker separates what a release depends on (the controls) from how often one model falls for one attack. That second question is answered with promptfoo and garak against the real model.

## How to check it
- `just redteam-check`: 61 tests (9 scenarios, 52 linked) across 10 categories, 0 problems.
- `uv run pytest -m redteam services/agent-api/tests/test_redteam_scenarios.py`, with OPA and the policies loaded: 9 passed.
- `just redteam` locally, with only OPA running: 57 executed, 53 held. The 4 that failed are live tests that need Keycloak and Alertmanager; they fail closed and are covered by CI's full stand-in. The 4 gVisor tests did not run because this tier has no sandbox runtime.
- `uv run pytest libs/agent-evals/tests/test_redteam.py services/agent-api/tests/test_redteam_suite.py`.

## Risks and follow-ups
- The scenarios cover the platform's controls, not a particular model's susceptibility. That is measured by promptfoo and garak in the next commit.
- Filing findings as issues, and the release workflow, come in the following commit.
