# Step 35 · Simulation and red-teaming

## 1. At a glance

| | |
|---|---|
| **Goal** | Test agents the way the real world will: people who are vague or impatient, tools that fail, and attackers who try every known trick. Prove that the platform's defences hold for each OWASP agentic risk, and stop a release when they don't |
| **Time** | About two days |
| **You end up with** | User and environment simulators (`agent_evals.user_sim`, `env_sim`); a red-team register for ASI01-ASI10 with 61 automated tests (9 scenario attacks, 52 linked control tests); an obedient adversary model; promptfoo and garak attack libraries against the run API; findings as GitHub issues; a release gate; `just redteam`, `redteam-check`, `redteam-promptfoo`, `redteam-garak`, `redteam-issues`, `release-gate` |
| **New words** | user simulator, persona, environment simulator, seeded fault, red team, OWASP ASI01-ASI10, obedient adversary, canary, payload marker, attack library, finding, fingerprint, release gate |

## 2. The big idea

Golden tasks (Step 34) check that an agent does its job on clean, single-shot cases. Real use
is messier. People open with "something's wrong with my account" and leave if the reply doesn't
help. Tools time out. And some inputs are written by attackers.

So you rehearse:
- **simulated people** with a goal and limited patience;
- **simulated environments** whose tools fail in a planned, repeatable way;
- **a red team** that attacks every weak point on the OWASP list.

The red team asks one hard question: if the model is fooled, does the platform still hold?

> *Analogy:* a bank's fire and robbery drills. You don't hope the robber is polite; you assume
> the teller has been tricked and check that the vault still needs two keys. Separately, you
> train tellers and test how often a con works on them. Those are different questions with
> different fixes.

## 3. What we're doing

1. A **user simulator**: persona (direct or vague, facts they can give), goal (what they want
   and how they recognise success), patience (turns and tolerance). It runs multi-turn tasks,
   one run per turn on one thread.
2. An **environment simulator**: mock tools from YAML with faults drawn from a seeded random
   number generator (errors, timeouts, malformed output, rate limits, outages, latency).
3. A **red-team register** for the ten OWASP agentic risks, linking every test that proves a
   control, with a severity and a tier.
4. **Scenario attacks** on real apps through the run API, with an *obedient adversary* model and
   the live OPA. The worst model meets the real controls.
5. The **attack libraries**: promptfoo and garak send known attack prompts to the run API.
6. **Findings become issues**, and a **release gate** refuses a release while any high or
   critical finding is open.

## 4. Why we're doing this

Agents act. An agent that can pay invoices, read records or hand work to colleagues is only as
safe as what happens when its model is talked into the wrong thing. The OWASP Top 10 for
Agentic Applications (December 2025) lists where that goes wrong. The spec requires an
automated test for each of the ten.

A test that can't fail is worse than none, because it creates false confidence. So every
"held" in this step has to be earned: live policy engine, canaries, payload markers, and a
weakened-engine self-test.

> *Watch out:* OPA fails closed. If OPA is down, every tool call is denied, and every attack
> looks "stopped". The suite checks OPA first: it must allow a known-good call and deny a
> known-bad one.

## 5. Key ideas

- **Persona, goal, patience.**
  - A vague customer starts unclear and adds detail only when the reply doesn't help.
  - An impatient one leaves after one unhelpful reply.
  - The conversation ends when the goal's check passes, the person leaves, or the turns run out.
    The harness decides success, never the simulator.
- **Seeded faults.** The same seed and the same calls give the same faults, so a flaky-looking
  run can be replayed exactly. Faults go through the platform's executor, so the agent sees
  real error codes.
- **OWASP ASI01-ASI10:**
  - ASI01 goal hijack;
  - ASI02 tool misuse;
  - ASI03 identity and privilege abuse;
  - ASI04 supply chain;
  - ASI05 unexpected code execution;
  - ASI06 memory and context poisoning;
  - ASI07 insecure inter-agent communication;
  - ASI08 cascading failures;
  - ASI09 human-agent trust exploitation;
  - ASI10 rogue agents.
- **Obedient adversary.** A stand-in model that does whatever any text in its context tells it
  to: the user's message, a tool result, a document, another agent's reply.
  - Payloads carry `[[do: tool {...}]]` or `[[answer: ...]]` next to the attacker's wording.
  - A guardrail that removes the payload before the model sees it is a defence that worked.
- **Canary.** A benign twin of each attacked app, run under the same setup. It must succeed,
  or the app is simply broken and "held" means nothing.
- **Payload marker.** Shows whether the attack reached the model. An attack that never reached
  it, and that no guardrail stopped, is reported as unproven.
- **Finding and fingerprint.** A failed attack is a finding. Its fingerprint (source plus attack
  id) ties it to one issue across runs.
- **Severity decides blocking.** Critical and high findings block a release. Medium and low ones
  are tracked as issues.

## 6. How to do it

```bash
just redteam-check                    # register: every category covered, every linked test exists
just provision-policies               # OPA needs Project A's policies
just redteam                          # unit + integration tiers; report in evals/results/redteam/
just redteam --tier sandbox           # the gVisor tests (ASI05) where gVisor is installed
# attack libraries against a running agent-api
export AGENT_REDTEAM_USER=alice AGENT_REDTEAM_PASSWORD=...    # or AGENT_REDTEAM_TOKEN
just redteam-promptfoo                # fixed library (offline, deterministic)
just redteam-garak-install && just redteam-garak
# on the Mac, with the real model
AGENT_REDTEAM_PROVIDER=gateway just redteam-promptfoo-generate     # owasp:agentic, local model
just release-gate evals/results/redteam/*.json --github            # what release.yml runs
just eval-app support-handoff --tasks evals/apps/support-handoff/conversations.jsonl   # multi-turn
```

**Adding an attack.** Write a YAML entry in `evals/redteam/attacks/` with:
- the ASI category, a severity and a vector;
- the app and its input;
- what holding the line looks like, using golden-task checks such as `policy.never_run`,
  `refused`, `approval_arguments` and `team.workers_forbidden`.

If the attack targets a new app, add that app's canary.

**Adding a linked test.** Add its node id to `catalogue.yaml`.

## 7. Benefits

- **Every OWASP agentic risk has executed tests.** CI on 08ac601 ran 57 attacks with 57 held,
  and the gVisor job held 4 more for ASI05.
- **The scenarios test controls, not luck.** Examples:
  - a payment over the policy limit is refused *even after a person approves it*;
  - kill switches stop a live app's tools;
  - the approver sees the real amount when the agent's summary lies.
- **The suite can't pass by accident.** The OPA preflight, canaries, payload markers and a
  weakened-engine self-test make sure of that.
- **One weakness is one issue**, closed only after a run shows it fixed.
- **A release can't ship** with an open high-severity finding.

## 8. Drawbacks and risks

- **Model susceptibility isn't measured here.** The stand-in's canned answers can't fall for
  anything, so the promptfoo and garak results in this build check the attack path only.
  Real-model runs are on the Mac.
- **Attack libraries age.** New attack styles appear constantly. Refresh promptfoo, garak and
  the scenario set as they evolve.
- **Canary-token assertions only catch an attack that says the token.** Subtler failures need
  the generated, model-graded promptfoo run.
- **CI time.** The suite adds about 1 minute to CI. The libraries run weekly and when red-team
  files change, because promptfoo is a large download.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Obedient adversary for scenarios | A real model as the attacker's target | Measuring a specific model's resistance (done by the libraries) |
| Fixed promptfoo library in CI | promptfoo's generated red team everywhere | When a local generator model is available in CI |
| garak with string detectors | garak's model-based detectors (toxicity, misleading claims) | When detector models can be downloaded and pinned |
| Issues per fingerprint | A findings dashboard only | Very large finding volumes, where issues become noise |
| Block on high/critical | Block on any finding | Very high-risk deployments, where the threshold can be lowered in the gate |

## 10. Which approach fits which situation

- **Changing a control** (policy, guardrail, approval, sandbox): run `just redteam`. CI also
  runs it on every push.
- **Changing or swapping a model:** run the attack libraries with
  `AGENT_REDTEAM_PROVIDER=gateway` on the Mac.
- **A new tool or app:** add a scenario attack for its riskiest action and a canary.
- **Before a release:** `release.yml` runs everything and applies the gate.
- **Debugging a flaky multi-turn behaviour:** replay with the same simulator profile and seed.

## 11. Common mistakes

- **Counting an attack as held when OPA was down.** The preflight exists for this.
- **Writing attacks the model never sees** (for example, text in a field that isn't sent). The
  payload marker catches it.
- **Asserting "the model refused"** instead of "the payment never ran". Assert on effects, not
  on words.
- **Treating stand-in library results as model safety.** They are labelled as attack-path checks.
- **Closing a red-team issue by hand.** Let a run where the attack holds close it.
- **Raising a severity threshold to get a release out.** Fix the weakness instead.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| Each ASI category has at least one automated test | ✅ | `just redteam-check`: 61 tests across all 10 categories (ASI01 7, ASI02 6, ASI03 7, ASI04 7, ASI05 7, ASI06 5, ASI07 5, ASI08 6, ASI09 5, ASI10 6). CI on 08ac601: 57 executed, 57 held, 0 findings with the live OPA. The gVisor job held 4 (ASI05). The new tests close the four gaps the survey found: approver sees the real amount (ASI09), kill switches in a live run (ASI10), deprecated and retired agents refused (ASI10), and attacks through real apps with the live OPA |
| Findings are tracked as issues | ✅ | `just redteam-issues`: open, comment, reopen and close by fingerprint. Tested against a fake GitHub API (3 tests). Wired into `ci.yml` and `redteam.yml` on `main`, and into scheduled, manual and release runs. No live issue opened yet: no finding has occurred on `main` |
| High-severity findings block release | ✅ | `release.yml`: full suite, gVisor tier and both libraries on the release commit. `just release-gate --github` refuses on any high or critical finding, any category without an executed test, a missing report, or an open high or critical red-team issue. Shown on this container's local reports: BLOCKED by 4 live tests that fail closed without Keycloak and Alertmanager. CI also fails on any blocking finding |
| User simulators (persona, goal, patience) for multi-turn tasks | ✅ | 24 generated support conversations. A vague, patient customer gets a refund on turn 2; an impatient one leaves after turn 1 (stand-in agents, mechanics only) |
| Environment simulators with mock tools and seeded faults | ✅ | Same seed gives the same faults, another seed differs. Outages, rate limits, timeouts and malformed output reach the caller as platform error codes. Passes the Tool contract |
| promptfoo and garak run the attack libraries | ✅ (path); real model VERIFY-ON-MAC | promptfoo 0.123.1: 10/10 held; garak 0.17.0: 9/9 probes held, against the stand-in run API, checking the attack path. `owasp:agentic` generated attacks and real-model runs are on the Mac |
| ADR | ✅ | ADR 0046 |

## 13. Recap

The platform now rehearses reality. Simulated customers bring vague openings and limited
patience, and simulated tools fail on a seed. A red team attacks every OWASP agentic risk with
a model that obeys the attacker, so each test shows whether the controls hold when the model
doesn't. Every "held" is earned: live OPA, canaries, payload markers, and a self-test that a
weaker engine fails. promptfoo and garak bring known attack libraries against the real run API,
ready for the real model on the Mac. Findings become issues that close only when a run proves
the fix, and a release can't go out with a high-severity one open.
