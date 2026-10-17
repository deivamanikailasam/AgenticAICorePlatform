# The three reference applications

Each one proves a pattern, and each runs through the same run API, policy, approvals, budgets and
audit as any agent. What they measure is in [`docs/results.md`](../results.md). The general
runbooks apply to them (failures, latency, cost, approvals backlog), and
[kill-switch.md](kill-switch.md) stops any of them.

| App | Pattern | Risk | Owner | What a person must do | Budget per run |
|---|---|---|---|---|---|
| `knowledge-assistant` 0.1.0 | Agentic RAG on Project 0's `rag-core` | low | agent-platform | Nothing: it only reads, on the user's behalf | 16 steps, 100,000 tokens, AUD 0.50, 300 s |
| `research-report` 0.2.0 | Hybrid: a supervisor team (researcher, analyst, writer, critic) as a subgraph, then publishing | medium | agent-platform | Approve the publication (`group:approvers`), never the person who asked | 12 steps, 60,000 tokens, AUD 0.50, 600 s for the app; each worker has its own |
| `invoice-triage` 0.3.0 | Hybrid: deterministic validation, an agent for exceptions, payment | medium | finance-platform | Approve each payment (`group:finance-approvers`) | 25 steps, 60,000 tokens, AUD 0.50, 300 s |

## knowledge-assistant

- **Answers without sources, or "I don't know" too often.** Search is on the user's behalf
  (token exchange), so it finds only what that user may read. Check the user's groups in
  Keycloak and the documents' clearance in `rag-core` before the agent.
- **`rag-core` down.** The tool call fails, the circuit breaker opens, and runs fail with a tool
  error ([tool-errors.md](tool-errors.md)). It is Project 0's service: escalate to its owner.
- **A document tries to give instructions.** Retrieved text is untrusted data. The prompt-
  injection guardrail screens it; [guardrail-blocks.md](guardrail-blocks.md) covers a spike.

## research-report

- **A run waits at `publish`.** That's the design: the report was written and reviewed and
  waits for a person. It shows in `GET /v1/approvals` for `group:approvers`. Approve, edit, or
  reject (nothing is published on reject). The requester's own approval is refused.
- **The critic keeps sending the report back.** The team stops after `max_rounds` (8), with the
  reason in its result's `stop_reason`. Without a report the run fails and nothing is published.
  With one, the report still waits for approval: the approver should read the critic's
  `review.issues` in the run's events before approving.
- **The team's run.** Its run ID is `<run>/research`, and its workers' are
  `<run>/research/<worker>-<round>`. One trace holds the app, the team and the workers.
- **Where published reports go.** `reports/<tenant>/<run>.json` in the artefact store.
- **Canary.** 0.2.0 is a 10% canary candidate in prod beside 0.1.0. A rollback is automatic
  ([release-rollback.md](release-rollback.md)).

## invoice-triage

- **Payments waiting.** Each payment waits for `group:finance-approvers`; see
  [approvals-backlog.md](approvals-backlog.md). An approver can edit the amount; the payment then
  runs with exactly the edited arguments.
- **Many invoices sent to the agent.** Validation is deterministic; only exceptions reach the
  agent. A jump in exceptions usually means the supplier data changed, not the agent.
- **A paid invoice paid again.** It can't be: `post_payment` needs an idempotency key, and a
  repeated key returns the first result. If it seems to have happened, freeze the agent with the
  kill switch and rebuild the runs from the audit trail (`just audit-replay <run_id>`).
