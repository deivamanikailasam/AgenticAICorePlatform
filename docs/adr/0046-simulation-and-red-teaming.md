# ADR 0046: Simulation and red-teaming: simulated people and environments, an ASI01-ASI10 suite run against the worst model, attack libraries against the real one, findings as issues, and a release gate

- **Status:** Accepted
- **Date:** 2026-10-16
- **Step:** 35
- **Deciders:** repository owner (delegated the open decisions, 2026-10-16)

## Context
Step 35 asks for:
- user simulators (persona, goal, patience) for multi-turn tasks;
- environment simulators with mock tools and seeded faults;
- red-team suites mapped to the OWASP Top 10 for Agentic Applications (ASI01-ASI10);
- promptfoo and garak running the attack libraries.

Done when:
- each ASI category has at least one automated test;
- findings are tracked as issues;
- high-severity findings block release.

Many controls already have tests from earlier steps (policy, approvals, guardrails, sandbox,
memory policy, signed cards, budgets, the registry gate). A survey of those tests found four
gaps:
- nothing showed that the approver sees the real arguments when the agent's summary says
  otherwise;
- the kill switches were tested only in Rego, never in a run;
- deprecated and retired agents were never shown to be refused;
- there was no test against the live policy engine through a real app.

This build has no real models. The stand-in gateway gives canned answers.

## Decision
1. **Simulators live in `libs/agent-evals`.**
   - `UserSimulator`: a scripted person (deterministic) and a model-played person. Success is
     always decided by the goal's check, never by the simulator.
   - Environment: mock tools defined in YAML with faults drawn from a seeded random number
     generator. They are real `Tool` components, so faults go through the platform's executor.
   - A multi-turn task is one run per turn on one thread.
2. **Scenario attacks assume the worst model.** An *obedient adversary* model follows any
   instruction it can see: in the user's message, a tool result, a document or another agent's
   output. A scenario therefore tests the platform's controls, not a model's resistance.
   - Payloads carry a marker. An attack whose payload never reached the model, and that no
     guardrail stopped, counts as unproven, not held.
   - Scenarios run through the run API on real reference apps with their real tools.
3. **A "held" result has to be earned.**
   - The policy engine is the live OPA, checked first: it must allow a known-good read and deny
     a known-bad cross-tenant read. OPA fails closed, so a dead OPA would otherwise make every
     attack look stopped.
   - Each attacked app's benign canary must succeed under the same setup.
   - A self-test shows that a weaker engine turns the value-limit and kill-switch attacks into
     findings.
4. **One register.** `evals/redteam/catalogue.yaml` lists the ten categories with their
   controls, plus the existing control tests, each with a severity and tier. Scenario attacks
   live in `evals/redteam/attacks/`. A pytest plugin turns every test into held, finding or not
   run. `just redteam-check` runs in `just check`: every category must have tests, and every
   linked test must still exist.
5. **Attack libraries test the real model through the whole platform.**
   - A standard-library adapter makes each attack prompt one run through the run API.
   - promptfoo 0.123.1 runs a fixed, offline library of canary-token attacks in CI. On the Mac
     it runs its `owasp:agentic` framework, with attacks generated and graded by the local
     model; remote generation, sharing and telemetry are always off.
   - garak 0.17.0 runs from its own environment, with CPU-only torch and probes whose detectors
     are string or pattern matches.
   - Their results use the same finding format.
6. **Findings are issues, matched by fingerprint.** A new finding opens an issue. A repeat
   finding comments on its issue, or reopens it. An attack that holds in a later run closes it.
   Issues are synced on `main` and on scheduled, manual and release runs, never from feature
   branches.
7. **Release gate.** `release.yml` runs the full suite (unit, integration and gVisor) and both
   libraries on the commit to be released. It refuses the release on any of: a high or
   critical finding; a category without an executed test; a missing report; an open high or
   critical red-team issue. CI also fails on any blocking finding, on every push.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Attack only with a well-behaved scripted model | It shows nothing about the controls: a model that never tries the attack never meets the control |
| Only real-model red-teaming (promptfoo/garak) | Measures one model's susceptibility, which changes with every model swap, and says little about whether a payment is still gated when the model is fooled. Both are kept, for different questions |
| promptfoo's remote attack generation | Sends the target's purpose and attack material to an external service; local generation through Project 0's gateway keeps it on the Mac |
| garak inside the workspace | Brings torch and its model stack into every environment; an isolated environment keeps the platform's dependencies small |
| garak probes with model-based detectors (toxicity, misleading) | Each needs a detector model download; the chosen probes decide with strings and patterns, so CI runs offline and results are exact |
| A severity threshold on rates (e.g. allow 1% hits) | Invites tuning the threshold instead of fixing the weakness; any hit is a finding, and its severity decides whether it blocks |
| Opening issues from every branch | Noise from work in progress; branches fail CI instead |
| A separate red-team register per tool | Splits coverage; one register shows all ten categories and their evidence in one place |

## Consequences
- **Evidence.**
  - CI on 08ac601 (full stand-in, live OPA): 57 attacks executed, 57 held, 0 findings across
    all ten categories (unit and integration tiers). The 4 gVisor tests ran in the sandbox job.
  - Locally: promptfoo 10/10 held and garak 9/9 probes held against the stand-in run API. These
    check the attack path, not model behaviour.
- **VERIFY-ON-MAC:** real-model susceptibility. Run `just redteam-promptfoo-generate`, and
  `just redteam-promptfoo` and `just redteam-garak` with `AGENT_REDTEAM_PROVIDER=gateway`. The
  real model may produce findings the stand-in cannot.
- **Owner:**
  - the repository needs GitHub Issues enabled for the issue sync;
  - publishing jobs added later must depend on the release gate.
- **Cost:**
  - the suite adds about 1 minute to CI's integration job;
  - the libraries run weekly and when red-team files change (promptfoo is a large download);
  - a release runs everything once.
