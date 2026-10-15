# ADR 0039: Human-in-the-loop through an approval desk: policy routing, SLAs, edits, escalation, two people

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 28
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 28 asks for:
- an `ApprovalGate` that pauses before risky actions, with approve, reject, edit-arguments and
  escalate;
- approvals routed by OPA to the right group, with SLA timers;
- reviewers who see the proposed action, reason, data and risk;
- audited decisions;
- review-after (sampled QA) and take-over patterns;
- alternatives: LangGraph interrupts for short waits, Temporal signals for long waits.

Done when:
- an irreversible tool always waits for approval;
- an edited approval runs with the edited arguments;
- an expired approval cancels safely.

Steps 15, 17 and 19 already paused runs at approval nodes, bound approval evidence to the exact
call, and checked it in policy.

## Decision
1. **An approval desk in the run API.** When a run pauses:
   - OPA routes the approval (`data.agent.approvals.route`). It returns the approver group
     (two-person rule, then the tool's own group, then the spec's group, then the default), the
     SLA per risk tier, and the escalation group.
   - An `ApprovalRequest` is filed with the gate: Postgres in deployments, in memory otherwise.
   - The interrupt carries what reviewers need: the proposed action, reason, input data, risk,
     side effect, expiry and routing reasons.
   - If OPA is down, the approval still pauses on a default route; it is never auto-approved.
2. **Decisions:**
   - **Approve** may carry edited arguments for a single action. The call then runs with
     exactly those, and the evidence is bound to their digest, so policy authorises what will
     run.
   - **Reject** runs nothing.
   - **Escalate** moves the request to the escalation group with a fresh SLA.
   - **Two-person:** the first approval is recorded and the run waits for a second, different
     approver from the two-person group, neither of them the requester. Policy (OPA and Cerbos)
     can now be satisfied by two people. Before this change it could never be satisfied.
3. **SLA timers.** A sweeper checks every 30 s. An overdue approval escalates once (if policy
   names a group), then expires: the run is cancelled with `approval expired` and nothing
   behind the approval runs.
4. **An irreversible tool always waits.** The assembly refuses to build any plan in which an
   irreversible tool (tool node or agent node) can run without an approval step. Policy still
   requires approval evidence for irreversible calls at execution time.
5. **Policy alignment.** The tool decision's approver group reads the same per-tool overrides as
   routing. A payment now needs `finance-approvers`; a general approver is no longer enough.
   The rules PDP and the Cerbos policy mirror this, and live parity tests pass.
6. **Review-after and take-over.**
   - A spec's `review.sample_rate` sends a deterministic sample of successful runs to a QA
     queue, with ok/issue verdicts audited.
   - An operator, or the run's approver group, can take over: the agent stops, the person gets
     the handover and may finish with their own answer (`taken_over`).
7. **Short and long waits.**
   - API-run agents pause with LangGraph or Agent Framework interrupts. The desk's records are
     durable, so a wait survives a restart.
   - `durability: temporal` agents wait on Temporal signals with a durable timer (Step 19).
     Routing the worker's approvals through the desk comes with API dispatch of Temporal agents
     (Step 19 follow-up).

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Approvals only as policy `require_approval` at call time | The run would fail instead of waiting, with no reviewer view or SLA |
| Approver group from the spec only | The spec author would pick their own approvers; policy must own routing |
| Expiry = auto-reject and continue | "Cancel safely" is clearer: the run stops and nothing ambiguous happens after the deadline |
| Editing by re-running the agent with a hint | Non-deterministic; editing the exact call keeps the human decision exact and auditable |
| Two-person as two separate approval nodes | Duplicates plans; the desk collects both decisions for one call |

## Consequences
- More approval friction for payments (finance group only) and for high-risk irreversible
  actions (two people). This is deliberate.
- The SLA sweeper runs in each API process; the Postgres row lock lets only one decision win.
  Leader election is a production follow-up (Step 37).
- Edits apply to single-action approvals. A batch of agent calls is approved or rejected as a
  whole.

## Verification
- `services/agent-api/tests/test_hitl.py`, 10 tests:
  - an unapproved irreversible tool is refused (422);
  - the inbox shows the action, reason, data, risk, group and the 60-minute medium-risk expiry;
  - an edited approval pays AUD 40 instead of 42;
  - the SLA escalates, then expires, the run is cancelled and nothing is paid;
  - a reviewer escalates, and only the leads may then decide;
  - a rejection runs nothing;
  - a high-risk payment needs two different people, with a 30-minute SLA;
  - review-after sampling and verdicts work, and sampling is deterministic;
  - an operator take-over pays nothing and records the operator's answer.
- Policy: 45 rego tests (routing, two-person, tool group) and 12 Cerbos tests; live Cerbos/OPA
  parity with the new cases; the rules router matches `opa eval` on 5 routing cases.
- `approval_gate.postgres`: the shared contract plus restart survival, against the stand-in.
