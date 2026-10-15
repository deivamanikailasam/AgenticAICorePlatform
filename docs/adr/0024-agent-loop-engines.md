# ADR 0024: The single tool-calling agent — native ReAct loop, an agent_loop slot, framework engines, Google ADK via A2A

- **Status:** Accepted (amends ADR 0022, point 6, for Google ADK)
- **Date:** 2026-10-15
- **Step:** 13
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 13 asks for a ReAct loop (model → tool calls, parallel when allowed → observation → model)
ending in a final structured answer, a budget limit or an approval interrupt, with schema repair,
a text ReAct fallback for models without native tool calling, and golden tool tasks. ADR 0022
deferred the OpenAI Agents SDK, Pydantic AI and Google ADK adapters to this step.

Versions checked 2026-10-15 (PyPI), cooldown rule of ADR 0022: openai-agents 0.24.0 is the
latest (2026-10-15), **0.22.3** (2026-09-17) pinned; pydantic-ai-slim 2.56.0 latest, **2.52.0**
(2026-09-30) pinned; google-adk 2.12.0 latest. **Every google-adk release from 2.10.0 to 2.12.0
requires `opentelemetry-api/sdk <=1.42.1`**, while the platform pins 1.45.1 (ADR 0010).

## Decision
1. **The loop is one plan node** (`NodeKind.AGENT` with `loop: react`), built by
   `pattern_builder.react` together with an approval node it routes to on `needs_approval`.
   Pausing and resuming therefore reuse the engines' existing interrupts (both runtime
   adapters), and the journal and saga of Step 12 apply unchanged.
2. **Parallel tool calls only when every call in the turn is read-only**; writes run in order,
   each with its own idempotency key. Tool output reaches the model marked untrusted.
3. **Repairs are bounded:** malformed calls (unknown tool, unparseable or schema-invalid
   arguments) are returned to the model as errors, at most `max_repairs` times; a final answer
   failing `output_schema` gets one repair; then the node fails cleanly.
4. **Limits stop, they do not fail:** `StopRun` (now in the contracts package) gives run status
   `stopped` with the partial transcript on both adapters.
5. **Text ReAct fallback** is chosen from the model's capabilities (or forced by config).
6. **`agent_loop` slot** (the 25th): engines are built from the platform's model client, tool
   executor and registry, so models stay on the gateway and tools under policy whatever engine
   runs. `AgentLoopContract` defines what an engine must do; specs choose with
   `components: { agent_loop: ... }` (default `native`).
7. **Framework engines in-process:** OpenAI Agents SDK and Pydantic AI, each with a model bridge
   onto the platform model client, tools wrapped around the platform tool executor, and their own
   telemetry export disabled. Tools needing approval are refused by these engines (only the
   native loop can pause a plan).
8. **Google ADK out-of-process via A2A (Step 21), not in-process.** Downgrading the platform's
   telemetry to satisfy one optional engine would couple every service to ADK's pin; a separate
   service with its own lock, called over A2A under the same policy and audit as any remote
   agent, keeps both. `agent_loop.google_adk` is a documented stub until then.
9. **Golden tool tasks** (`evals/golden/react-tools`, 8 cases): in CI with a deterministic
   stand-in model (plumbing, not skill); with the real local model on the Mac (VERIFY-ON-MAC),
   reported with the dataset size.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| The loop as many small graph nodes (think/act edges) | Dynamic tool sets and repairs make the graph noisy; one node keeps plans readable |
| Unlimited parallel tool calls | Writes in parallel can race and break ordering assumptions |
| Framework engines calling providers directly | Bypasses role mapping, keys, budgets and cost accounting on the gateway |
| Downgrade OpenTelemetry to 1.42.1 for ADK | Couples the whole platform to an optional engine's pin |

## Consequences
Approvals inside framework engines are not supported; such agents use the native loop. The
real-model golden score is unknown until run on the Mac.

## Verification
`libs/agent-runtime/tests/test_agent_loop.py` (14 + contract), `test_engine_openai_agents.py`,
`test_engine_pydantic_ai.py`, `libs/agent-compiler/tests/test_react_agent.py` (golden tasks on
three engines × two adapters, text fallback, `max_steps`, approval interrupt and resume).
