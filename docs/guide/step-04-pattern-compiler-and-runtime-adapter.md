# Step 4 · Pattern compiler and runtime adapter skeleton

## 1. At a glance

| | |
|---|---|
| **Goal** | Turn a spec into a runnable plan and run it end to end, traced |
| **Time** | About a day |
| **You end up with** | `libs/agent-compiler` (`compile_spec`, graph pattern builders, approval insertion, `runtime_for`, traced runner), `just compile`, `just run`, a trace in Tempo |
| **New words** | compiler, executable plan, pattern builder, runtime adapter, span, trace |

## 2. The big idea

The spec says *what* should happen. The compiler works out *the exact steps and hand-offs*,
and the runtime adapter is the engine that performs them. Keeping these apart means we can
change the engine (LangGraph today, something else later) without touching any spec.

> *Analogy:* an architect's drawings (spec) become a construction schedule (plan); any qualified
> builder (runtime adapter) can follow the schedule. The site inspector's log (the trace)
> records each task as it happens.

## 3. What we're doing

1. `compile_spec(spec) → ExecutablePlan`: choose the PatternBuilder for `pattern:`.
2. Register graph builders for `workflow`, `state_machine` and `hybrid`.
3. Expand `agent` nodes using their inner pattern's builder.
4. Insert an approval node before every tool that requires approval.
5. Validate the plan and stamp metadata and a `plan_hash`.
6. `runtime_for(spec)` picks the adapter by name; the stub adapter is `sequential`.
7. Run with OpenTelemetry spans (`invoke_agent <name>`, `execute_node <id>`) exported to the
   Collector, and check the trace in Tempo.

## 4. Why we're doing this

This step proves the whole pipeline — spec, compiler, runtime, telemetry — on the simplest
possible case before any model is involved. Enterprises care because "what exactly ran" must be
answerable later: the plan hash and the trace are that answer.

> *Why this matters:* safety gates added by the compiler cannot be forgotten by a pattern author
> or skipped by a runtime. The approval node is in the plan for everyone to see.

## 5. Key ideas

- **ExecutablePlan.** A flat graph of typed nodes and conditional edges, with an entry and exits.
- **PatternBuilder.** A plug-in that turns one pattern's section of the spec into plan nodes.
- **Runtime adapter.** The interface (`build`, `invoke`, `stream`, `resume`, `cancel`,
  `get_state`) every engine implements.
- **Span and trace.** A span is one timed operation; a trace is the tree of spans for one run.
  The OpenTelemetry GenAI conventions name the root span `invoke_agent <agent>`.
- **Plan hash.** A fingerprint of the plan's nodes and edges; same spec, same hash.

## 6. How to do it

```bash
just compile agents/hello-workflow/agent.yaml            # print the plan as JSON
just run agents/hello-workflow/agent.yaml '{"name":"Ada"}'
# {"status": "succeeded", "output": {"greeting": "hello, Ada"}, "trace_id": "…"}
```

Check the trace (Mac: Grafana → Explore → Tempo, search the trace id; CI/cloud: the stand-in):

```bash
curl -s 127.0.0.1:3200/api/v2/traces/<trace_id> | jq '.trace.resourceSpans[].scopeSpans[].spans[].name'
# "execute_node greet"
# "invoke_agent hello-workflow"
```

Key files: `libs/agent-compiler/src/agent_compiler/compiler.py` (compile pipeline),
`patterns/graph.py` (graph builders), `run.py` (traced runner), `tracing.py` (OTel setup),
`cli.py` (`agent-compile`). See `docs/architecture/run-sequence.md` for the sequence diagram.

## 7. Benefits

- One tested path from spec to running code and trace.
- Approval gates are guaranteed by construction.
- Engines are swappable: the plan does not depend on LangGraph.
- `plan_hash` gives lineage a stable id.

## 8. Drawbacks and risks

- Flattening agent nodes makes plans larger and node ids longer.
- Lowest-common-denominator plan features: an engine-specific trick needs a new node kind.
- Tracing is minimal until Step 30 (no token metrics yet).

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Own plan format | Compile straight to LangGraph | A single-engine shop that will never switch |
| Compile-time approvals | Runtime-only approval checks | Never alone; Step 10 adds policy-time checks *as well* |
| OTLP to the Collector | Direct Langfuse SDK | Never; vendor lock-in (ADR 0010) |

## 10. Which approach fits which situation

- **Explicit process:** `pattern: workflow` with a graph.
- **Process with agentic steps:** `pattern: hybrid` and `agent` nodes; each agent node's
  `pattern` must have a builder installed (`react` arrives in Step 13).
- **State-driven process (statuses and transitions):** `pattern: state_machine`.

## 11. Common mistakes

- Expecting `just run` on `invoice-triage` to work now: its agent node needs the `react` builder
  (Step 13). The error says so.
- Forgetting to restart the Collector after changing its config (the container keeps the old one).
- Looking for traces before the batch exporter flushes; the runner flushes at the end of a run.

## 12. Done when

| Check | Status | Evidence |
|---|---|---|
| A "hello" workflow spec compiles | ✅ | `just compile agents/hello-workflow/agent.yaml` |
| … and runs end to end through the stub adapter | ✅ | `just run …` → `succeeded`, `hello, Ada` |
| … with a trace in Tempo | ✅ stand-in · **VERIFY-ON-MAC** | `test_trace_in_tempo.py` finds `invoke_agent hello-workflow` and `execute_node greet` in Tempo 3.1.0 via the Collector |
| Pattern plug-in per `pattern:` value; `RuntimeAdapter` interface | ✅ | graph builders registered; adapter interface in `agent-contracts` |
| ADR | ✅ | ADR 0014 |

## 13. Recap

Step 4 connected the pieces: `compile_spec` turns a validated spec into a flat, hashed
`ExecutablePlan` using the pattern builder registered for its `pattern`, expands agent nodes,
inserts approval gates before risky tools, and `just run` executes the plan on the stub runtime
adapter while emitting an OpenTelemetry trace that lands in Tempo.
