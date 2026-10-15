# ADR 0033: Supervisor teams as child runs with typed shared state and policy on every delegation

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 22
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 22: a supervisor agent delegates to worker agents, collects their results and decides when
the task is done.
- Each worker is a sub-graph with its own spec, tools and budget.
- Hierarchies nest supervisors.
- Shared state is explicit and typed, and workers receive only what they need.
- An LLM supervisor is the default, with a rules-based dispatcher as the alternative.

Done when: the research-report reference app runs with three workers, a worker's tool cannot be
called by another worker, and per-agent cost shows in the trace.

OWASP ASI07 (insecure inter-agent communication) asks for OPA on delegation and messages
treated as data. ASI08 (cascading failures) asks for budgets that roll up, delegation limits and
circuit breakers.

No new packages were needed.

## Decision
1. **One SUPERVISOR plan node** for `pattern: supervisor` or `hierarchical`. A hierarchy is a
   worker whose spec is itself a supervisor; nesting is bounded by `max_depth`.
2. **Workers are child runs of their own specs.** A worker factory builds each one its own model
   roles, registry and grants, so a worker literally does not have another worker's tools:
   - the agent loop refuses unknown tools;
   - policy refuses tools outside its grants, even if they were registered by mistake.

   The API's assembly is the factory.
3. **Typed shared state.** The spec declares state fields as JSON Schema, and each worker
   declares the fields it `reads` and `writes`. A worker gets only the task and its reads. Only
   its writes are kept, each validated; everything else it returns is dropped. A worker is not
   started until its reads exist.
4. **Policy on every delegation.** The PDP decides `agent.delegate` (OPA rule, version
   2026-10-15.3), with the team as grants and a per-agent kill switch, and it is audited.
5. **Worker output is data.** The LLM dispatcher sees state values delimited as untrusted. Its
   answer is constrained by JSON Schema to a team worker or `FINISH`.
6. **Bounded cascades.** The team's `max_cost_aud` covers all workers' metered spend. A worker
   that fails `max_failures` times is taken out (circuit breaker). Rounds are capped. A team
   that ends without its `output` stops instead of succeeding.
7. **Per-agent cost in the trace.** Each worker run is an `invoke_agent <worker>` span in the
   same trace, with AUD cost and tokens. The result has `cost_by_agent`.
8. **Dispatchers.**
   - `llm` (default): the supervisor's planner role chooses.
   - `rules`: no model; the first worker whose writes are missing and whose reads are ready.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Workers as agent nodes inside one graph, sharing one tool executor | One grant set for all workers: exactly the cross-worker tool use this step must prevent |
| LangGraph's supervisor helpers | Engine-specific. Our runtimes must behave the same, and policy, grants and metering are platform concerns |
| Free-form shared scratchpad | Untyped state lets any worker overwrite anything, including what the supervisor relies on |
| Trust worker output as supervisor instructions | A compromised worker could steer the team (ASI07) |

## Consequences
- Worker runs are not checkpointed separately. A crash re-runs the current worker; tools stay
  idempotent.
- Cerbos covers tool calls only. Delegation with Cerbos needs an `agent` resource policy
  (follow-up).
- The dispatcher sees each state value truncated to 4,000 characters.

## Verification
- `libs/agent-compiler/tests/test_supervisor.py` (11 tests): three-worker run with per-agent
  cost spans; reads-only inputs; cross-worker tool refused at the loop and at policy; unknown or
  denied delegation; worker output as data; circuit breaker; budget cap; max_depth; rules
  dispatcher.
- `services/agent-api/tests/test_team_api.py`: through the API, each worker is assembled with
  only its own tool, and delegations are audited.
- `just policy-test`: three delegation Rego tests (31/31).
