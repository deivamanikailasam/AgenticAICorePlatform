# The invoice-triage reference app runs end to end

**Step:** 15 · **Commit:** feat(apps): add the invoice-triage hybrid reference app · **Date:** 2026-10-15

## What changed
- `apps/invoice-triage/` (a new workspace package) completes the spec's example agent:
  - `invoice.validate` checks currency, GST (exactly 10% of the subtotal, to the cent), the
    total and the purchase order, and reports every problem;
  - `invoice.payment_details` takes the agent's resolution when there was one, otherwise the
    validated invoice;
  - a demo ERP as an MCP server (`get_invoice`, `post_payment`) that keeps its own rules: no
    unknown invoices, no other currency, never more than the invoice total, never twice.
- Functions can now be registered by name (`agent_platform.functions` entry points), so the spec
  keeps `fn: invoice.validate`.
- `agents/invoice-triage/agent.yaml` gains the agent's instructions and answer schema, the
  `prepare_payment` step and the payment's arguments. The payment argument is `amount_aud`,
  the name the policy's payment limit uses.
- `just demo-invoice-triage INV-1002 j.smith` runs it against Project 0 with the real model, OPA
  and rag-core.

## Why
The spec's "Done when" for Step 15: the hybrid reference app runs end to end. It shows workflow
steps and an agent working together, with policy on every tool call and a person approving the
irreversible payment.

## How to check it
`uv run pytest apps/invoice-triage` — on both runtimes, with the demo ERP over real MCP and every
call under policy and audit:
- a valid invoice waits for approval, then is paid exactly once (no model call at all);
- an invalid invoice goes to the agent, which reads the invoice and the payment policy (citation
  kept), proposes AUD 528, and is paid after approval;
- a rejected approval pays nothing;
- the requester approving their own payment is refused by policy.

## Risks and follow-ups
The model is scripted in these tests; the real model's behaviour is VERIFY-ON-MAC
(`just demo-invoice-triage`). The approver's identity comes from the caller until the run API
(Step 17) takes it from a verified token.
