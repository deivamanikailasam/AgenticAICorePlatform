# Step 11 · Runtime adapter

## 1. At a glance

| | |
|---|---|
| **Goal** | Run compiled plans on a real orchestration engine, and prove the engine can be swapped |
| **Time** | About two days |
| **You end up with** | `LangGraphAdapter` (default), `AgentFrameworkAdapter` (second engine), shared node semantics, the `WorkflowRuntimeContract`, a CrewAI stub, and one spec proven identical on both engines |
| **New words** | runtime adapter, superstep, reducer, interrupt, fan-out / fan-in, recursion limit, dependency cooldown |

## 2. The big idea

The compiler (Step 4) writes a recipe card: the plan. The runtime adapter is the kitchen that
cooks it. Step 11 hires a professional kitchen (LangGraph) instead of the toy one from Step 4,
and then cooks the same recipe in a second, very different kitchen (Microsoft Agent Framework)
to prove the recipe, not the kitchen, decides what ends up on the plate.

> *Analogy:* a song written as sheet music. Any good orchestra can play it; if two orchestras
> play the same notes, you know the music lives in the score, not in one band.

## 3. What we're doing

1. Write the exam every engine must pass: the workflow runtime contract.
2. Put what a node *does* in one shared module, so engines only decide *when* nodes run.
3. Build the LangGraph adapter: state with reducers, parallel branches, joins, interrupts,
   streaming, cancellation, recursion limit from the budget.
4. Build the Agent Framework adapter on the same shared module.
5. Compile one `agent.yaml` and run it on both engines; compare.

## 4. Why we're doing this

Orchestration (parallelism, pausing, resuming, retries) is hard and already solved by mature
engines. The platform's value is the governed layer around it: budgets, approvals, policy,
tracing. Keeping the engine behind an adapter avoids lock-in: if LangGraph's direction or
licence changes, specs keep working on another engine.

> *Watch out:* an adapter that "interprets" nodes its own way slowly changes behaviour. That is
> why node semantics live in one module shared by every adapter.

## 5. Key ideas

- **Superstep.** Every node that is ready runs together; then the engine moves on. Two branches
  from the same node run in parallel in one superstep.
- **Reducer.** A rule for merging updates into the run's state. Parallel branches each add their
  own value; nothing is overwritten.
- **Outcome routing.** Each node finishes with an outcome (`ok`, `error`, `rejected`, or a
  function's `_outcome` such as `big`). An edge is taken when its condition is empty, equals the
  outcome, or is `on_<outcome>`. An `error` with no matching edge fails the run.
- **Join.** Waits until every branch leading into it has finished (LangGraph: a deferred node;
  Agent Framework: a fan-in edge group).
- **Interrupt.** An approval node pauses the run; `resume` continues with the reviewer's answer.
- **Recursion limit = `max_steps`.** A runaway loop stops with status `stopped`, keeping its
  partial state.
- **Cancellation.** Each run executes in its own task; `cancel` stops it even in the middle of a
  node, and whoever was waiting gets a `cancelled` result.
- **Dependency cooldown.** New dependencies are pinned to the newest release that is at least 14
  days old, unless a newer one fixes a vulnerability.

## 6. How to do it

Choose the engine per spec (default `langgraph`):

```yaml
runtime: { adapter: langgraph, durability: memory }       # or: agent_framework
```

Run a spec:

```bash
just run agents/hello-workflow/agent.yaml '{"name":"Ada"}'
```

Test a new adapter against the exam:

```python
from agent_contracts.testing import WORKFLOW_FUNCTIONS, WorkflowRuntimeContract


class TestMyAdapter(WorkflowRuntimeContract):
    def make(self, functions=None):
        return MyAdapter(functions={**WORKFLOW_FUNCTIONS, **(functions or {})})
```

Register it as an entry point `"runtime_adapter.my_engine" = "my_pkg:MyAdapter"`.

## 7. Benefits

- Real parallelism, pausing and resuming from a mature engine, with the platform's rules intact.
- A second engine already works, so switching is a one-line spec change, not a rewrite.
- New engines have a ready-made exam with known answers.

## 8. Drawbacks and risks

- Two engines to keep upgraded; the shared contract shows regressions immediately.
- State is in memory until Step 18 (durable checkpoints).
- On Agent Framework, edges into a join must be unconditional (the adapter refuses otherwise).

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| LangGraph (default) | Microsoft Agent Framework | Microsoft-centred estates (Azure, .NET agents); implemented and contract-tested |
| LangGraph | OpenAI Agents SDK, Pydantic AI, Google ADK | Agent-loop-first designs; built with the agent patterns in Step 13 |
| LangGraph | CrewAI | Role-based "crew" prototypes; stub (catalogue) |
| Memory checkpoints | Postgres / Temporal | Anything beyond tests: Steps 18 and 19 |

## 10. Which approach fits which situation

- **Most agents and workflows:** LangGraph.
- **Organisation standardised on Microsoft's agent stack:** `adapter: agent_framework`.
- **Very long runs (hours to days) or exactly-once side effects:** Temporal durability (Step 19),
  whichever adapter runs the steps.
- **Evaluating a new engine:** implement the adapter, pass `WorkflowRuntimeContract`, then run
  the same-spec test against it.

## 11. Common mistakes

- Expecting a join to wait for a branch that a condition skipped: on Agent Framework it cannot;
  use unconditional edges into joins.
- Setting `max_steps` too low for a plan with parallel branches: each superstep counts once.
- Writing node logic inside an adapter instead of the shared node module.
- Upgrading LangGraph to a release from yesterday: follow the cooldown.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| The same spec passes the same tests on LangGraph and on the second adapter for the workflow pattern | ✅ | `test_same_spec_both_adapters.py`: one `agent.yaml` (parallel checks, join, routing) gives identical state and finished nodes on both, for both routes; both adapters pass the same 14 contract tests |
| Compiled plan → `StateGraph` with typed state and reducers | ✅ | `LangGraphAdapter`; parallel branches merge without loss (`A+B+C`) |
| Interrupts | ✅ | approve and reject paths resume correctly on both engines |
| Streaming | ✅ | `node_started` / `node_finished` with outcomes, `approval_requested` |
| Cancellation | ✅ | a 30-second node is cancelled in well under 5 seconds |
| Recursion limit tied to the budget | ✅ | `max_steps: 5` stops a loop with status `stopped` |
| Second adapter | ✅ | Microsoft Agent Framework (`agent-framework-core` 1.19.0), not a stub |
| `hello-workflow` on the default engine | ✅ | `just run` and the Tempo trace test pass on LangGraph |
| ADR | ✅ | ADR 0022 |

## 13. Recap

Step 11 moved plan execution onto real engines. Node behaviour lives in one shared module;
LangGraph runs plans with typed, merged state, parallel branches, joins, interrupts, streaming,
cancellation and a recursion limit from the budget. Microsoft Agent Framework runs the same
plans through the same module and passes the same exam, and one spec compiled once gives
identical results on both. The engine is now a choice, not a commitment.
