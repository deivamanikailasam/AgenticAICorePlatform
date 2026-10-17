# Sequence: an irreversible action waits for a person (Steps 15, 28)

The compiler inserts an approval node in front of every tool the spec marks irreversible. The
run can't skip that node, and policy refuses the tool call without approval evidence bound to
the exact arguments.

```mermaid
sequenceDiagram
  autonumber
  participant R as Requester
  participant API as agent-api
  participant RT as Runtime (plan)
  participant D as Approval desk
  participant OPA as OPA
  participant AP as Approver
  participant T as Tool executor
  participant AU as Audit (hash-chained)
  R->>API: POST /v1/agents/{agent}/runs
  API->>RT: run plan
  RT->>RT: reach approval node before the irreversible tool
  RT-->>API: interrupt (proposed call, arguments)
  API->>D: file the request
  D->>OPA: route? (approver group, SLA, escalation, two-person)
  OPA-->>D: route
  D->>AU: approval.requested
  API-->>R: AG-UI event: waiting for approval
  AP->>API: GET /v1/approvals
  AP->>API: POST /v1/runs/{id}/approvals {approve, optional edited arguments}
  API->>D: decide (approver ≠ requester; second approver if two-person)
  D->>AU: approval.decided (arguments digest)
  API->>RT: resume with the evidence
  RT->>T: call tool (idempotency key)
  T->>OPA: allow? (subject, tool, arguments, evidence)
  OPA-->>T: allow only if the evidence matches these arguments
  T->>AU: tool.call
  T-->>RT: result (untrusted data)
  RT-->>API: run finished
  API-->>R: AG-UI events
```

- **Reject:** nothing runs. The run follows its rejection path.
- **No answer within the SLA:** the request is escalated once, if policy names an escalation
  group. If that also runs out, the run is cancelled safely (`approval.expired`).
- **Shadow runs** never ask for approval. Their tools that write answer "shadowed".
