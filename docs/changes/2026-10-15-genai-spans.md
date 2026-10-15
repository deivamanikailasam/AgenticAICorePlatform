# GenAI spans for every agent, model call and tool call; content only to Langfuse

**Step:** 30 · **Commit:** feat(agent-telemetry): GenAI spans across the run API, content only to Langfuse · **Date:** 2026-10-15

## What changed
- **New library `libs/agent-telemetry`.** It follows the OpenTelemetry GenAI conventions, pinned to commit `f3c6d0c` of `open-telemetry/semantic-conventions-genai`. That repository has no release yet, and the conventions are in Development status. It provides:
  - `agent_span` / `end_agent`: `invoke_agent {agent}`, INTERNAL, with the agent's name, id, version, conversation, run, tenant, status, token usage and AUD cost.
  - `TracingModelClient`: `chat {model}`, CLIENT. Provider `litellm`, request and response model, max tokens, temperature, finish reasons, token usage, error type. Streaming calls are covered too.
  - `TracingToolExecutor`: `execute_tool {tool}`, INTERNAL. Tool name, call id, type (`function`, or `extension` for MCP and A2A), the agent, and the outcome (`error.type` on refusals and failures).
  - `ContentFreeExporter`: drops the opt-in content attributes (`gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.definitions`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`) before spans reach the collector and Tempo.
  - `build_provider` / `setup`: turn content capture on only when a Langfuse exporter exists. Content is redacted at the source: secrets always, PII through Presidio when configured. It is withheld if redaction is unavailable.
  - `telemetry_emitter.otel`, which passes the `TelemetryEmitter` contract.
- **Run API.**
  - Every run, and every resume, gets an `invoke_agent` span. Team workers run through `run()`, which now uses the same global provider, so a multi-agent run is one trace.
  - The assembly wraps each agent's model and tools with the tracing wrappers.
  - agent-api and agent-worker call `setup_from_env`. Compose passes `P0_OTEL_OTLP_ENDPOINT`, `LANGFUSE_HOST` and `LANGFUSE_PUBLIC_KEY`; the Langfuse secret key comes from OpenBao.
- **MCP.** Client spans now follow the MCP conventions: `tools/call {tool}`, CLIENT, with `mcp.method.name`, `server.address` / `server.port` and `error.type`. When an agent calls a tool, they sit under the platform's `execute_tool` span.
- **Step 29 follow-up.** The credential leak test now also checks the run API's own spans.

## Why
Step 30: "Spans follow the OpenTelemetry GenAI conventions … MCP spans use the MCP conventions. Prompt and response content goes to Langfuse only, redacted and access-controlled, never to general logs."

## How to check it
- `uv run pytest libs/agent-telemetry services/agent-api/tests/test_telemetry.py`. These check:
  - nesting, kinds and attributes;
  - error types;
  - streaming;
  - content redacted, sent only to the Langfuse exporter, and withheld when redaction fails;
  - the emitter contract;
  - a three-worker team run through the API is one trace, with each `chat` and `execute_tool` span under the agent that made it and no content.
- `just test-integration services/agent-api/tests/test_credentials_in_runs.py`. The run API's spans contain no credential values.

## Risks and follow-ups
- The conventions are in Development status, so attribute names may change. The pinned commit makes any change a reviewed one.
- A resumed run (after an approval) starts a new trace. It is linked to the first by the conversation and run ids, not by a span link.
