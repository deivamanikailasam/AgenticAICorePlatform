# Agents can run on the OpenAI Agents SDK or Pydantic AI, under platform rules

**Step:** 13 · **Commit:** feat(agent-runtime): add OpenAI Agents SDK and Pydantic AI loop engines · **Date:** 2026-10-15

## What changed
- `agent_loop.openai_agents` (OpenAI Agents SDK 0.22.3) and `agent_loop.pydantic_ai` (Pydantic AI
  slim 2.52.0) run the agent loop on those frameworks. For both:
  - the framework's model interface is **bridged onto the platform model client**, so every call
    goes through the LiteLLM gateway by role and is costed; no provider SDK, key or direct OpenAI
    call is involved;
  - each granted tool is a framework tool whose body calls the **platform tool executor** (policy,
    rate limits, audit, idempotency key `<run>:<node>:<n>`);
  - **framework telemetry export is off** (`tracing_disabled` for the Agents SDK; no Logfire for
    Pydantic AI); the platform's own tracing applies;
  - tool output returns to the model as untrusted data, `max_iterations` stops the run, and
    `output_schema` is enforced;
  - tools that need approval are refused (only the native loop can pause and resume a plan).
- Both pass `AgentLoopContract`, and all 8 golden tool tasks pass on each engine on both runtime
  adapters.
- `agent_loop.google_adk` is a documented stub: ADK pins `opentelemetry-sdk<=1.42.1`, so ADK
  agents join as separate A2A services with their own dependency lock (Step 21, ADR 0024).

## Why
Teams that already build agents with these frameworks can keep their code style while the
platform keeps the controls: which models, which tools, which policy, what gets logged.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_engine_openai_agents.py libs/agent-runtime/tests/test_engine_pydantic_ai.py libs/agent-compiler/tests/test_react_agent.py`

## Risks and follow-ups
Framework releases move fast; both are pinned to releases at least 14 days old (ADR 0022) and
upgraded deliberately. Streaming through the framework bridges arrives with the agent API
(Step 17).
