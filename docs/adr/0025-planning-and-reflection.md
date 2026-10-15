# ADR 0025: Planning and reflection patterns as strategies on one agent node, with a capped model and a metered comparison

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 14
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 14 asks for plan-and-execute (re-plan on failure), ReWOO (plan once, run tools without
re-prompting), reflection / self-critique and evaluator-optimiser with bounded rounds, each a
`PatternBuilder`, with the Planner and Critic roles swappable. Done when: each pattern runs a
reference task and reports cost and steps against ReAct on the same task, with numbers only from
real runs, stating the dataset and model.

No new third-party dependency is needed (checked 2026-10-15): the strategies use the platform
model client, tool executor and `jsonschema` already pinned. LangGraph and Agent Framework both
offer planner/reflection examples, but adopting them would tie the patterns to one runtime.

## Decision
1. **Each pattern is one `AGENT` plan node** with `loop: <pattern>`, built by its own
   `pattern_builder.<pattern>` (subclasses of the ReAct builder). The runtime adapters are
   unchanged; an `AgentNodeDispatcher` routes agent nodes to the strategy named by `loop`. The
   patterns work as a whole spec or as an `agent` node inside a hybrid graph.
2. **Planner and critic are slots** (`planner.llm`, `critic.llm`, chosen by
   `components: { planner, critic }`). Plans and critiques are structured output; a plan naming a
   tool the agent was not granted is refused.
3. **One cap for everything:** each node run wraps the model client in `CappedModel` with the
   node's `max_iterations`; planner, critic and ReAct sub-steps all use it, so the cap is the
   total model calls for the node. Reaching it raises `StopRun` (run status `stopped`).
4. **ReWOO arguments refer to evidence** (`#E1`, `#E1.field`), substituted before each tool call;
   tools still go through the authorising executor, with idempotency key `<run>:<node>:<n>`.
   Evidence reaches the solver labelled as data, not instructions.
5. **Plan-and-execute sub-steps are ReAct runs limited to the step's one tool** (4 model calls
   each), using the native loop; a failed step triggers a re-plan of the remaining steps, at most
   `max_replans` (default 2).
6. **Reflection uses the agent's own role** for critique; **evaluator-optimiser uses `judge`**
   and returns the best-scoring candidate. Both stop at `max_rounds` (default 2) or when a
   critique passes.
7. **Approval-requiring tools are refused** by these patterns (they cannot pause mid-plan);
   such agents use `pattern: react` with the native loop.
8. **Per-call copies:** each node run works on a shallow copy of the strategy, so parallel agent
   nodes never share planner, critic or cap state.
9. **Comparison harness** (`agent_compiler.compare`, `just compare-patterns`): same tasks, tools
   and model for every pattern; metering at the model-client and tool-executor boundary (model
   calls, tool calls, tokens, AUD cost from the gateway). Output always states dataset path,
   size, model alias and time. Stand-in runs prove plumbing only and are never reported.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Each pattern as a graph of small nodes (plan → step₁…stepₙ → solve) | Plans are dynamic; re-planning would need graph rewriting at run time. One node keeps the journal and saga simple |
| Framework-specific planners (LangGraph plan-and-execute, Agent Framework magentic) | Ties the pattern to one adapter; the strategies here run identically on both |
| Separate caps for planner, critic and steps | Easy to exceed the budget in total; one cap is what the spec's `max_steps` means |
| ReWOO with approval tools (pause between tool runs) | Breaks "run tools without re-prompting"; use plan-execute or react for such agents |

## Consequences
Step-level journal replay applies to the whole agent node, not to individual plan steps; durable
per-step checkpoints arrive in Step 18. Token and cost budgets per run (beyond the call cap) come
in Step 26. The real comparison numbers are unknown until `just compare-patterns` runs on the Mac.

## Verification
`libs/agent-runtime/tests/test_reasoning.py` (planner and critic contracts),
`libs/agent-runtime/tests/test_strategies.py` (7: ReWOO two calls, re-plan, self-critique role,
best candidate, cap covers all calls, approval refusal, evidence substitution),
`libs/agent-compiler/tests/test_compare_patterns.py` (all five patterns × 3 reference tasks ×
both adapters, metered).
