# ADR 0015: Model access per role through the gateway, with httpx and AUD cost conversion

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 05
- **Deciders:** repository owner

## Context
ADR 0008 routes every model call through Project 0's LiteLLM gateway. Step 5 must decide how
agents name models, which client library talks to the gateway, how structured output is made
reliable, and how cost is reported in AUD.

## Decision
1. **Roles, not models.** Nodes name a role; resolution is spec `models:` → `platform.yaml`
   `models.roles` → the name itself. Six roles: default, planner, executor, router, judge,
   summariser. Each role uses its own LiteLLM virtual key (`LITELLM_KEY_<ROLE>`).
2. **Client.** `httpx` (0.28.x) against the OpenAI-compatible `/v1/chat/completions` API, not the
   OpenAI SDK: fewer dependencies, full control of retries and SSE parsing, trivially mocked with
   respx. The client lives in `agent-runtime` (`agent_runtime.models`), which the spec's layout
   introduces in Step 11; it is created now for this purpose.
3. **Capabilities.** `platform.yaml` records tool calling, structured output, streaming and
   context window per alias. The client refuses incompatible requests; the compiler refuses
   incompatible specs.
4. **Structured output.** `response_format: json_schema` (strict), validated locally with
   jsonschema; one repair attempt quoting the validation error; then `StructuredOutputError`.
5. **Retries.** Exponential backoff on transport errors, 408/409/425/429/5xx, up to
   `gateway.max_retries` (default 2). Other 4xx fail fast.
6. **Cost.** Tokens from `usage`; cost from LiteLLM's `x-litellm-response-cost` header (USD),
   converted with `currency.usd_to_aud` (1.4434 from AUD/USD 0.6928 on 2026-10-01,
   exchange-rates.org); recorded per run and role in a `UsageLedger` (persisted in Step 26).

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| OpenAI Python SDK (3.31.0) | Heavier; retries and streaming behaviour less visible; fine if we needed Assistants/Responses APIs |
| LiteLLM Python SDK in-process | Bypasses the gateway (violates ADR 0008) |
| Raw model ids in specs | Every model change edits every spec |
| No repair attempt | Small local models occasionally emit near-JSON; one repair is cheap and bounded |
| Live FX lookup per call | Non-deterministic costs and an extra dependency; a dated rate is auditable |

## Consequences
Model changes are a `platform.yaml` edit. Capability values must be kept accurate. Costs in AUD
carry the rate's date.

## Verification
`libs/agent-runtime/tests/test_gateway.py` (contract + behaviour), `test_gateway_integration.py`
(stand-in), `test_real_models.py` (Mac only), compiler capability tests.
