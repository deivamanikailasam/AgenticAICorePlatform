# A2A gateway: each agent on its own routes; approvals reported as input-required

**Step:** 24 · **Commit:** fix(a2a-gateway): scope routes per agent and report pending approvals · **Date:** 2026-10-15

## What changed
- Each published agent's card, JSON-RPC and HTTP+JSON routes now sit in their own Starlette
  `Mount` at `/agents/<name>`.
- The SDK's REST routes include a catch-all `/{tenant}` mount, which is now dropped. The tenant
  comes from the verified token, never from the URL.
- When a run waits for approval, the A2A task becomes `input-required`. Its message now says
  what is pending, for example "Approve calculator?". It is read from the AG-UI interrupt
  outcome, which the previous code did not read.
- New tests:
  - a second published agent, `gated-desk`, with an approval-gated tool reports
    input-required;
  - each agent serves its own card;
  - no `/{tenant}` route is left.

## Why
With two published agents, the first agent's `/{tenant}` mount matched every later path. The
gateway admitted a call for agent B (token and OPA checked for B), but agent A's handler ran it.
That routes work to an agent the caller was not authorised for. The bug was found by adding the
second agent for the approval test.

## How to check it
`uv run pytest services/a2a-gateway/tests`: 10 tests, all passing.

## Risks and follow-ups
The A2A caller cannot approve. Approvals stay with the platform's approvers, through the run API
or its UI. This is deliberate, and documented in the Step 24 chapter.
