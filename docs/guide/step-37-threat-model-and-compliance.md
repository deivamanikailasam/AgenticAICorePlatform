# Step 37 · Threat model and compliance

## 1. At a glance

| | |
|---|---|
| **Goal** | Write down everything that could go wrong with the platform, who is responsible, what stops it and which test proves it. Show how that lines up with the laws and standards that apply. Handle people's data from creation to deletion |
| **Time** | About a day |
| **You end up with** | `governance/threat-model.yaml` (49 STRIDE threats, 11 components); `governance/controls.yaml` (22 controls with evidence and framework mapping); `just threat-check` in `just check`; generated `docs/security/threat-model.md` and `control-matrix.md`; data-flow diagrams; dated compliance sources; `governance/data-lifecycle.yaml` with export and retention in the run API |
| **New words** | threat model, STRIDE, trust boundary, control, control matrix, evidence, data lifecycle, retention, right of access |

## 2. The big idea

A threat model is a list of "what could go wrong", per part of the system. Each item needs an
owner, a defence and proof that the defence works. A control matrix turns that around: for each
defence, where is the evidence, and which legal or standards requirement does it help meet?

Here both are data files that CI checks. The moment a test is renamed or a control removed, the
build says which threat just lost its proof.

> *Analogy:* a building's fire safety file. It lists every risk (kitchen, wiring, exits), who
> looks after it, what's installed (sprinklers, alarms), and the inspection records. The
> inspector checks the records, not the promises.

## 3. What we're doing

1. List the components and their trust boundaries: where data comes in from someone you don't
   control.
2. For each component, go through STRIDE: spoofing, tampering, repudiation, information
   disclosure, denial of service, elevation of privilege. Each threat gets an owner, controls,
   OWASP risks and tests. Write down why any category doesn't apply.
3. List the controls with their evidence in the repository, and map them to the frameworks.
4. Draw the data flows. Set how long each kind of data is kept, and add export and retention.
5. Check all of it on every commit.

## 4. Why we're doing this

Agents act on people's behalf and handle their data, so security and privacy failures have real
consequences. Regulators and customers ask for exactly these artefacts:
- a risk register;
- evidence of controls;
- logs;
- human oversight;
- the right to see and delete your data.

Writing them as checked data means they can't go stale quietly.

> *Watch out:* laws are moving. The EU AI Act's high-risk start date is unsettled (2 August 2026
> in the original text; a reported deferral to December 2027 for some systems isn't confirmed).
> Australia's automated-decision transparency duties start on 10 December 2026. The sources page
> is dated; re-check before relying on it.

## 5. Key ideas

- **STRIDE** is a checklist of six kinds of attack, applied to each component so nothing is
  forgotten.
- **Trust boundary:** where data from someone you don't control enters. Input crossing one is
  checked (identity, policy) and treated as data.
- **Controls with evidence.** Each control lists code, policies, ADRs and at least one test.
  The checker makes sure they exist.
- **OWASP agentic mapping.** Threats name the ASI risks they belong to, so the agent-specific
  list (Step 35) and the general one (STRIDE) meet.
- **Framework mapping at the level that can be verified:**
  - NIST AI RMF functions;
  - ISO/IEC 42001 Annex A objectives;
  - EU AI Act articles;
  - Australian Privacy Principles;
  - the Australian Guidance for AI Adoption's six practices.
- **Data lifecycle.**
  - Finished runs and transcripts are kept 90 days.
  - Checkpoints are kept 30 days, with the newest per thread.
  - Memory lasts per kind.
  - The audit trail is kept about 7 years and holds no content.
  - A person can export their data; a privacy officer can erase it everywhere.

## 6. How to do it

```bash
just threat-check                     # every threat: owner, control, test; every control: evidence
just threat-model                     # regenerate docs/security/threat-model.md and control-matrix.md
# a person's data (Step 37)
curl -H "Authorization: Bearer $TOKEN" $API/v1/me/export                       # your own
curl -X POST -H "Authorization: Bearer $OFFICER" -d '{"user":"alice"}' $API/v1/admin/export
curl -X POST -H "Authorization: Bearer $OPERATOR" $API/v1/admin/retention      # scheduled in Step 38
```

**Adding a threat.** Add an entry under its component with `stride`, `owner`, `controls` and
`tests`, then run `just threat-model`.

**Adding a control.** Add it to `governance/controls.yaml` with evidence (including at least one
test) and its framework mapping.

## 7. Benefits

- **"Every threat has an owner, a control and a test" stays true.** CI fails the moment it
  isn't.
- **Auditors get a matrix where every cell links to code or a test,** not a claim.
- **People can get a copy of their data and have it deleted,** and old data is pruned on
  schedule. Each of these is audited.
- **One place to see where the OWASP agentic risks and the general STRIDE threats meet.**

## 8. Drawbacks and risks

- **The mapping isn't legal advice.** It is deliberately coarse, and a compliance review should
  refine it.
- **Retention values are owner policy.** Legal and privacy review must confirm them per
  deployment.
- **A threat model is only as good as the threats people think of.** Revisit it when components
  or data change, and after every incident.
- **Owner roles are all held by one person today.** Split them as the team grows.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Checked YAML | Threat Dragon or pytm diagrams | Larger teams who want visual modelling; keep the checker for the links |
| STRIDE per component | Attack trees per goal | Deep analysis of one high-value asset, such as payments |
| Coarse, verified framework mapping | Full clause-by-clause mapping | When the standards and legal review are at hand |
| API-applied retention | Database jobs | Bulk archives outside the platform's own data |

## 10. Which approach fits which situation

- **A new component or data store:** add it with its boundary, a STRIDE pass and tests before it
  ships.
- **A new tool with side effects:** add or extend the threat (tool misuse, tampering) and its
  red-team scenario.
- **A data request from a person:** export or erase through the API; both are audited.
- **An audit or customer questionnaire:** start from the control matrix and the compliance
  sources page.

## 11. Common mistakes

- Listing threats without tests. The checker refuses it.
- Marking a STRIDE category "not applicable" without a real reason.
- Copying clause numbers from memory. Cite what you verified, with a date.
- Deleting audit records for erasure. The audit trail holds no content; record the erasure
  instead.
- Pruning runs that are still waiting for a person.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| Every threat has an owner, a control and a test | ✅ | `just threat-check` (in `just check`): 49 threats across 11 components, each with an owner role bound to a person, known controls, and tests that pytest collects or that are red-team ids; 52 tests, 0 problems. Every STRIDE category per component is covered or explained. The checker's own tests show each rule failing on a bad model |
| The control matrix links to evidence in the repo | ✅ | `docs/security/control-matrix.md` (generated): 22 controls, each linking code, policies, ADRs and at least one test. Every path is checked to exist. Mapped to NIST AI RMF, ISO/IEC 42001, the EU AI Act, the APPs and the AU Guidance for AI Adoption, with dated sources |
| STRIDE per component plus OWASP agentic mapping | ✅ | All ten ASI categories appear in the threats' mapping (tested) |
| Data-flow diagrams | ✅ | `docs/security/data-flows.md`: trust boundaries, data by kind with storage, protection, destinations and lifetime |
| Data lifecycle: retention, erasure, export | ✅ | `governance/data-lifecycle.yaml`. Export: own data only; another user and another tenant refused; officer allowed; audited. Retention: an old finished run is pruned with its events, a waiting run is kept; audited. Erasure from Step 20 is unchanged |
| ADR | ✅ | ADR 0048 |

## 13. Recap

The platform's risks are now a checked list: 49 threats, each with an owner, a defence and a
test, across every component. Every defence links to the code and tests that prove it, and maps
to the frameworks a regulator or customer will ask about, with sources dated. People can export
their data and have it erased, and old data is pruned on schedule, never cutting off a run that
is waiting for someone. When code moves, the build points at any threat that lost its proof.
