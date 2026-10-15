# Step 28 · Human-in-the-loop

## 1. At a glance

| | |
|---|---|
| **Goal** | People decide before risky actions, on time, with the facts in front of them |
| **Time** | About two days |
| **You end up with** | An approval desk in the run API: OPA routing with SLAs; approve, reject, edit-arguments and escalate; two-person approval; safe expiry; a reviewer inbox; durable approvals in Postgres; review-after sampling and take-over |
| **New words** | approval desk, routing, SLA, escalation, two-person rule, edited approval, review-after, take-over |

## 2. The big idea

Some actions can't be undone: paying money, deleting records, emailing customers. Before those,
the agent stops and asks a person. The platform makes sure that:
- the **right** person is asked;
- they see **what exactly** will happen and why;
- they can **change** it, pass it up, or say no;
- if nobody answers **in time**, nothing happens.

> *Analogy:* a purchase-order system. The request goes to the right manager based on the amount
> and the department. The manager sees the supplier, the amount and the reason, and can approve,
> change the amount, refuse, or send it up. Big orders need two signatures, and a request left
> unsigned for too long is cancelled, not quietly approved.

## 3. What we're doing

1. Let policy (OPA) route each approval: which group, how long they have, who's next.
2. Build the approval desk: file requests, show them to reviewers, take decisions, run timers,
   and audit everything.
3. Support edited arguments, escalation and two-person approval, end to end and in policy.
4. Refuse any plan in which an irreversible tool could run without an approval step.
5. Keep approvals in Postgres so they survive restarts.
6. Add review-after (a sample of finished runs gets a human check) and take-over (a person
   finishes the job).

## 4. Why we're doing this

Guardrails (Step 27) miss things, and models make confident mistakes. A human decision before
irreversible actions is the brake that catches what the seatbelts don't. It also produces the
evidence regulators and auditors ask for: who approved what, when, and on what basis.

> *Watch out:* an approval is only as good as what the reviewer sees. Showing "Approve tool
> call?" without the amount, recipient and reason invites rubber-stamping.

## 5. Key ideas

- **Routing by policy.** Group precedence: two-person (high-risk irreversible), then the tool's
  own group (payments go to finance approvers), then the spec's group, then the default. The
  SLA follows the risk tier (60 minutes at medium by default).
- **Edited approval.** The reviewer can change the arguments, for example AUD 40 instead of 42.
  The call runs with exactly those, and the approval is cryptographically tied to them (a digest).
- **Escalation.** The reviewer, or the timer, passes the request to the escalation group with a
  fresh deadline.
- **Two-person rule.** For high-risk irreversible actions, two different people approve, neither
  of them the requester.
- **Safe expiry.** A missed deadline escalates once, then cancels the run. Nothing behind the
  approval runs.
- **Always gated.** The platform refuses to start a run in which an irreversible tool has no
  approval step.
- **Review-after and take-over.** A sample of finished runs goes to QA reviewers. An operator can
  stop an agent and finish the work themselves.

## 6. How to do it

```yaml
tools:
  - ref: mcp://erp/post_payment
    approval: { required: true, approvers: "group:finance-approvers" }
review: { sample_rate: 0.1, reviewers: "group:qa-reviewers" }
```

```bash
GET  /v1/approvals                                   # my inbox
POST /v1/runs/{id}/approvals {"decision": "approve", "arguments": {"amount_aud": 40}, "comment": "discount"}
POST /v1/runs/{id}/approvals {"decision": "escalate", "comment": "unusual supplier"}
POST /v1/runs/{id}/approvals {"decision": "reject"}
GET  /v1/reviews ; POST /v1/reviews/{id} {"verdict": "issue", "note": "..."}
POST /v1/runs/{id}/takeover {"answer": "...", "note": "..."}    # operator role
```

Routing and SLAs live in `policies/agent/config/data.json` (`approvals`), and the rule in
`policies/agent/approvals/route.rego`.

## 7. Benefits

- Irreversible actions never run without the right person's decision.
- Reviewers see the facts, can fix small problems instead of rejecting, and can pass things up.
- Deadlines can't turn into silent approvals.
- A complete audit trail of requests, decisions, edits, escalations, expiries, reviews and
  take-overs.

## 8. Drawbacks and risks

- Approvals slow work down. Route carefully, and keep approvals for actions that need them.
- Reviewers can still rubber-stamp. Use review-after samples to spot it.
- Edits apply to one action at a time.
- With several API replicas, each runs a timer. The database lock keeps decisions single, and
  leader election comes in Step 37.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Interrupts in the run API (short waits) | Temporal signals (long waits) | Approvals that may take days, across deployments and restarts at scale |
| Cancel on expiry | Auto-reject and continue | The agent has a sensible "not approved" path to take |
| Desk inside the run API | A separate approvals service | Several platforms share one reviewer inbox |
| Two-person by policy | One approver with a higher role | Small teams where two people aren't available |

## 10. Which approach fits which situation

- **Payments:** the finance group, an edit allowed (amounts), two people at high risk, and a
  short SLA.
- **Customer emails:** approval with edits to the text, plus review-after sampling.
- **Data deletion:** two-person, no edits, and expiry cancels.
- **Stuck or confused agents:** operator take-over, with the handover state for the person
  finishing the work.

## 11. Common mistakes

- Letting the spec author choose the approver group for sensitive tools. Policy should own it.
- Long or missing SLAs. A request nobody owns waits forever.
- Treating "no answer" as approval.
- Editing arguments outside the platform, which breaks the binding between approval and call.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| An irreversible tool always waits for approval | ✅ | A spec without an approval step for the irreversible `pay` tool is refused (422) before anything runs; with one, the run pauses and nothing is paid until a decision; policy also requires bound evidence |
| An edited approval runs with the edited arguments | ✅ | The reviewer approves `{"amount_aud": 40.0}` instead of 42: exactly AUD 40 is paid, evidence is bound to the edited call, and `approval.decided` records `edited: true` |
| An expired approval cancels safely | ✅ | After the 60-minute SLA, the request escalates to `group:approval-leads`; after the next 60 minutes it expires, the run is `cancelled` ("approval expired"), nothing is paid, a late decision finds nothing to approve, and `approval.expired` is audited |
| Routed by OPA with SLA timers | ✅ | `route.rego` with 5 rego tests; the rules router matches `opa eval` on 5 cases; the inbox shows the routed group and expiry |
| Approve, reject, edit, escalate; reviewers see action, reason, data and risk; audited | ✅ | 10 tests in `test_hitl.py`: inbox content, rejection, escalation (old group loses access), two-person approval |
| Review-after (sampled QA) and take-over | ✅ | Sampled run reviewed with an "issue" verdict; operator take-over with the handover; nothing pending ran |
| Durable approvals | ✅ | `approval_gate.postgres`: shared contract and restart survival (stand-in) |
| ADR | ✅ | ADR 0039 |

## 13. Recap

Risky actions now go through an approval desk:
- Policy picks the reviewers and the deadline.
- Reviewers see exactly what will happen, and can approve, edit, refuse or escalate.
- Two people sign off the riskiest actions.
- Missed deadlines cancel safely.

An irreversible tool can't run without an approval step, and samples of finished work plus
operator take-over keep people in charge afterwards too.
