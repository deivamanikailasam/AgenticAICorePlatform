# Streamed model calls are retried until the stream opens

**Step:** 33 · **Commit:** fix(agent-runtime): retry streamed completions before the first byte · **Date:** 2026-10-16

## What changed
`GatewayModelClient.stream` opens its request with the same retry policy as `chat`:
- transport errors (refused connection, timeout) and retryable HTTP statuses (429, 5xx, …) are retried with exponential backoff, up to `gateway.max_retries`;
- a non-retryable status fails at once with "gateway refused the stream (…)";
- giving up raises "gateway unavailable after N attempts (…)".

A stream that breaks after chunks have been delivered is not retried, because the caller has already used them.

## Why
The chaos tests found it. Workflow `llm` nodes stream, so a single connection blip or model timeout failed the step at once (`explain: ConnectError: connection refused`), while the same failure on a non-streamed call was retried. Retrying before the first byte is safe: nothing was produced, and the gateway's own retries and fallbacks still apply.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_gateway.py`:
- a ConnectError then a ReadTimeout then success yields the full stream after 3 requests;
- a 400 is refused at once;
- a gateway that stays down gives up after 3 attempts.

The chaos test "model timeout" passes through the run API.

## Risks and follow-ups
A stream that dies mid-way still fails the step. Resuming a half-delivered stream would need the gateway to support it, which LiteLLM does not.
