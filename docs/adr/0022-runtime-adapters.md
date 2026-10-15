# ADR 0022: Runtime adapters — LangGraph default, Microsoft Agent Framework second, shared node semantics, dependency cooldown

- **Status:** Accepted; point 6 amended by [ADR 0024](0024-agent-loop-engines.md) (Google ADK via A2A)
- **Date:** 2026-10-15
- **Step:** 11
- **Deciders:** repository owner

## Context
ADR 0004 chose LangGraph 1.x as the default engine behind a `RuntimeAdapter` seam. Step 11 must
implement it, add a second adapter, and prove that the same spec passes the same tests on both
for the workflow pattern. The catalogue lists Microsoft Agent Framework 1.0, the OpenAI Agents
SDK, Pydantic AI and Google ADK as alternatives, and CrewAI as a stub.

Versions on PyPI, checked 2026-10-15: langgraph 1.2.15 (2026-10-12), 1.2.14 (2026-10-06), 1.2.12
(2026-09-21); agent-framework-core 1.21.0 (2026-10-08), 1.19.0 (2026-09-18). PyPI lists no known
vulnerabilities for langgraph 1.2.12, 1.2.14 or 1.2.15.

## Decision
1. **Dependency cooldown.** New direct dependencies are pinned exactly to the newest release that
   is at least 14 days old, unless a newer one fixes a known vulnerability. Fresh releases are
   where supply-chain attacks and regressions show up first. Hence **langgraph 1.2.12** and
   **agent-framework-core 1.19.0**. (Transitive versions come from `uv.lock`.)
2. **Shared node semantics** (`agent_runtime.nodes`): what a node does, the outcome rules, edge
   matching, the run output and `max_steps` live in one module that every adapter calls. Adapters
   only decide scheduling, parallelism, interrupts, streaming and cancellation, so engines cannot
   drift in behaviour.
3. **LangGraph adapter (default):** typed state with reducers (`values`/`outcomes` merge,
   `order` appends), conditional edges by outcome, deferred join nodes, `interrupt()` for
   approvals, recursion limit = `max_steps`, `debug` + `updates` stream modes, one task per run
   for cancellation, LangGraph checkpointer (memory now, Postgres in Step 18).
4. **Second adapter: Microsoft Agent Framework workflows,** not a stub. It is a real graph engine
   (executors, conditional edges, fan-in, request/response for human input, iteration limit),
   light (`agent-framework-core` has no model-provider dependencies), and very different
   internally from LangGraph, which makes it a strong proof of the seam. Restriction recorded:
   edges into a join must be unconditional; `build` refuses otherwise.
5. **Proof:** `WorkflowRuntimeContract` (parallel + join, routing, error edge, unhandled failure,
   `max_steps`, cancel mid-node, rejected approval, event stream, determinism) runs on both
   adapters; one `agent.yaml` is compiled and run on both with identical state.
6. **OpenAI Agents SDK, Pydantic AI, Google ADK:** these are agent-loop frameworks first. A
   workflow-only adapter for them would be the platform's own graph walker wearing their name.
   They are built with the agent patterns in **Step 13**, where an agent node can run on them,
   and they must pass the same contracts there. **CrewAI** is a documented stub, as the
   catalogue marks it: crews do not map onto plan graphs without losing interrupts, typed state
   and budgets.
7. `agents/hello-workflow` runs on LangGraph; the Step 4 `sequential` adapter remains a test
   double.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| A stub as the second adapter | The spec allows it, but a stub proves nothing about the seam |
| Pydantic Graph as the second adapter | Viable; it releases almost daily, so the cooldown pin would lag, and Agent Framework is the catalogue's first-listed alternative |
| Latest versions | Rejected by the cooldown rule above |
| Each adapter interprets nodes itself | Behaviour drifts between engines; tests would catch it late |

## Consequences
Two engines to keep upgraded; the shared contract makes regressions visible immediately. Both
keep state in memory until Step 18. Agent Framework's join restriction must be respected by
pattern builders that target it.

## Verification
`uv run pytest libs/agent-runtime/tests`: the LangGraph and Agent Framework contracts (14 tests
each), the same-spec test and the CrewAI stub test.
