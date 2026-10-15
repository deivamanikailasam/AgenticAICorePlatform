# ADR 0028: Run API — AG-UI over SSE (WebSocket alternative), events from inside the run, approvals from verified tokens

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 17
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 17 asks for `agent-api`: create run, stream events, get state, cancel, resume, list threads,
submit approval, with events emitted as AG-UI (text deltas, tool call start/end, state deltas,
approval requests) over SSE, idempotent run creation, and SSE → WebSocket as the alternative.
Done when: a minimal web page shows live tool calls and an approval button using only AG-UI
events. PROGRESS open issue 8 requires the approver's identity to come from a verified token.

Versions checked 2026-10-15 (PyPI), cooldown rule of ADR 0022: `ag-ui-protocol` 1.0.0
(2026-09-17) is the latest and older than 14 days; pinned. `playwright` 1.64.0 is 2 days old,
**1.63.0** (2026-09-15) pinned as a development dependency. No SSE library: Starlette's
streaming response (already locked) is enough.

## Decision
1. **AG-UI 1.0 as the event contract.** Platform run events are translated to AG-UI:
   `RUN_STARTED`/`STATE_SNAPSHOT`, `STEP_*`, `TEXT_MESSAGE_*`, `TOOL_CALL_START/ARGS/END/RESULT`,
   `STATE_DELTA` (JSON Patch), and `RUN_FINISHED` whose outcome is `success`, `cancelled` or
   **`interrupt`** (AG-UI's standard human-in-the-loop outcome) naming the proposed action, the
   arguments and the approver group; failures are `RUN_ERROR`.
2. **Events are collected inside the run**, not from adapter streams: an event-emitting node
   runner, a tool-executor wrapper and a streaming model wrapper report to a sink bound to the
   run's task. This works identically on LangGraph and Agent Framework and across resumes.
   Plain-text model replies stream token by token from the model client's stream.
3. **SSE by default, WebSocket as the alternative**, carrying the same numbered events. A stream
   ends when the run is idle (paused or finished); clients reconnect with `after=<last id>`.
   Tokens travel in the `Authorization` header (SSE via `fetch`, not `EventSource`) or the
   WebSocket's first message, never in URLs.
4. **Approvals from verified tokens.** The decision is built from the approver's token
   (username, groups); body fields are ignored. The requester cannot approve their own run; the
   approver must be in the interrupt's group (and policy checks again, ADR 0026); the generic
   resume refuses approval interrupts. Closes open issue 8.
5. **Ownership and isolation.** Runs belong to (tenant, user); others get 404, except approvers
   of the pending approval in the same tenant. Tenants come from the shared `tenant_of` rule.
6. **Idempotent creation** by `Idempotency-Key` per user: same body → same run; different body →
   409.
7. **Tool calls act for the requester** (subject token and caller identity bound to the run's
   task), under OPA or Cerbos (shared `pdp_factory`) and audited.
8. **Public vs internal issuer.** In containers, tokens are verified against the public issuer
   (`P0_KEYCLOAK_PUBLIC_URL`) with keys fetched internally; also fixed in the MCP gateway.
9. **State in memory** until Step 18 makes runs durable.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Platform-specific event JSON | Every UI would need a custom client; AG-UI clients (CopilotKit and others) work as they are |
| `EventSource` in the browser | Cannot send an `Authorization` header; tokens would end up in URLs and logs |
| WebSocket only | Proxies and corporate networks handle SSE better; WebSocket kept as the alternative |
| Approver fields in the request body | Anyone holding the run id could claim to be an approver |
| A custom `approval_requested` event | AG-UI 1.0 has a standard interrupt outcome; custom events would not be understood by AG-UI clients |

## Consequences
A restart loses runs until Step 18. A run resumed long after creation uses the requester's token,
which may have expired; delegated long-lived credentials are Step 29. Two-person approval, edits,
SLAs and expiry are Step 28.

## Verification
`services/agent-api/tests/test_api.py` (9), `test_settings.py`,
`libs/agent-runtime/tests/test_events.py` (4); integration: `test_api_integration.py` (the
invoice-triage agent through the real service with Keycloak and OPA; headless Chromium drives the
page through approval to payment).
