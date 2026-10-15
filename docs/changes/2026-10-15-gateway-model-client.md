# Talk to models only through the Project 0 gateway, per role

**Step:** 05 · **Commit:** feat(runtime): add the gateway ModelClient with roles, retries and AUD cost · **Date:** 2026-10-15

## What changed
`GatewayModelClient` (`model_client.litellm`, in the new `agent-runtime` package) is how every
agent talks to a model:

- the caller names a **role** (`planner`); it resolves through the spec's `models:` then
  `platform.yaml`, and the request uses that role's own LiteLLM virtual key;
- **tool calling** and **JSON-schema structured output**, refused up front if the model lacks the
  capability; a reply that doesn't match the schema gets **one repair attempt**, then fails clearly;
- **streaming** (text and tool-call pieces assembled, usage at the end);
- **retries** with backoff on network errors, 429 and 5xx; other errors fail fast;
- **usage and cost per call**: tokens from the reply, cost from LiteLLM's response-cost header,
  converted to AUD, and recorded per run and per role in a usage ledger.

The stand-in gateway gains a `local-qwen-json` mock alias for the structured-output check.

## Why
Every model call going through one switchboard means one place for budgets, fallbacks, cost
accounting and audit (ADR 0008). Roles with their own keys are like department cost codes on a
shared phone system: the bill says who spent what.

## How to check it
`uv run pytest libs/agent-runtime` (unit, with a mocked gateway) and, with the stand-in running,
`just test-integration` (all six roles answer; streaming; structured output).

## Risks and follow-ups
The structured-output check on the *real* local Qwen models runs only on the Mac:
`P0_REAL_MODELS=1 just with-secrets uv run pytest -m mac libs/agent-runtime` (VERIFY-ON-MAC).
