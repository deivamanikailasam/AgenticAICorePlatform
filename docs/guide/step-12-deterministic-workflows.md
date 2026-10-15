# Step 12 · Deterministic workflows

## 1. At a glance

| | |
|---|---|
| **Goal** | Fixed business processes from `agent.yaml`: code, model, tool and sub-workflow steps, branches, joins, timeouts, and undo steps when something fails |
| **Time** | About two days |
| **You end up with** | LLM, router, tool and sub-workflow executors; saga compensation; a run journal with replay; end-to-end tests on both runtime engines |
| **New words** | saga, compensation, journal, replay, idempotency key, value path |

## 2. The big idea

A workflow is a recipe with fixed steps, not an improvising chef. Most steps are plain code; a
few ask a model ("summarise this", "approve or reject?"); some use tools (look something up,
create a ticket); some call a shared sub-recipe. If a later step fails after money moved or
stock was reserved, the recipe says how to put things back, step by step, in reverse.

> *Analogy:* booking a holiday. You book the flight, then the hotel, then the car. If the car
> booking fails and you cancel the trip, you cancel the hotel first, then the flight: the newest
> booking is undone first.

## 3. What we're doing

1. Let specs name an undo step per node (`compensation:`).
2. Undo finished steps, newest first, when a run fails (the saga).
3. Record every step's result (the journal) and replay runs from it.
4. Add executors for LLM, router, tool and sub-workflow nodes.
5. Prove it all on both engines from real `agent.yaml` specs.

## 4. Why we're doing this

Deterministic workflows are the cheapest, safest and most testable way to automate a process;
agents (Step 13) are for the parts that genuinely need judgement. External systems cannot share
one database transaction, so undo has to be explicit. And when something goes wrong in
production, you want to replay exactly what happened without calling the real systems again.

> *Watch out:* a write without an idempotency key can happen twice on a retry. Tool nodes always
> use `<run id>:<node id>`.

## 5. Key ideas

- **Function, LLM, tool, router, join, approval, sub-workflow nodes** — each with one job.
- **Value paths.** `$input.text` or `${validate.total}` read the run's values. Nothing is
  evaluated; a model's answer placed into a later prompt stays text.
- **Router.** Can only return a route the spec declares, whether chosen by code or by a model.
- **Parallel branches and joins.** Several matching edges run at once; a join waits for all.
- **Timeouts.** `timeout_seconds` on a node turns a slow step into an `error` outcome that an
  `on_error` edge can handle.
- **Saga.** On failure, every finished step with a `compensation` has its undo run, newest first.
  A step that failed is not undone.
- **Journal and replay.** Every node result is recorded. A replay returns the recorded results,
  so the run takes the same path with no model, tool or function called.
- **Stable idempotency key.** A tool call's key is `<run id>:<node id>`, so a retry or replay of
  the same run can never repeat the side effect.

## 6. How to do it

A workflow with parallel model and tool steps, a router, and an undo step:

```yaml
tools: [{ ref: local://platform/word_count }]
graph:
  entry: start
  nodes:
    - { id: start, type: function, fn: my_pkg.flow.start, compensation: undo_start }
    - id: summary
      type: llm
      model: default
      config: { template: "Summarise in one line: $input.text" }
    - id: count
      type: tool
      tool: word_count
      config: { args: { text: "$input.text" } }
    - { id: both, type: join }
    - { id: size, type: router, fn: my_pkg.flow.long_or_short, routes: [long, short] }
    - { id: long, type: function, fn: my_pkg.flow.long }
    - { id: short, type: function, fn: my_pkg.flow.short, timeout_seconds: 5 }
    - { id: undo_start, type: function, fn: my_pkg.flow.undo_start }   # only runs on failure
  edges:
    - "start -> summary"
    - "start -> count"
    - "summary -> both"
    - "count -> both"
    - "both -> size"
    - "size -> long: long"
    - "size -> short: short"
```

A sub-workflow:

```yaml
- id: kyc
  type: subgraph
  spec: ../kyc-check/agent.yaml
  config: { inputs: { customer: "$input.customer_id" } }
```

Replay a run in code:

```python
recorder = NodeRunner(functions, executors)
await run(spec, inputs, ctx, runner=recorder)
journal = recorder.journal(ctx.run_id)  # save journal.to_json() with the run
replayer = NodeRunner(functions, executors, replay=journal)
await run(spec, inputs, new_ctx, runner=replayer)  # same path, nothing executed
```

## 7. Benefits

- Processes are declared, reviewed and versioned as YAML, and run the same on any engine.
- Failures leave the world consistent: completed writes are undone in order.
- Incidents can be replayed exactly, offline, as often as needed.
- Tool steps stay under the same policy, rate limits and audit as everything else.

## 8. Drawbacks and risks

- Undo steps are best effort: if an undo fails, the run stays failed and the failed undo is
  visible, but nothing retries it automatically yet.
- Journals and run state live in memory until Step 18.
- Approvals that policy requires on a tool node stopped the call (fail closed) until Step 15,
  which sends the approval node's decision to policy as evidence bound to the call (ADR 0026).

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Platform saga | Engine-native compensation (Temporal) | Very long runs; Step 19 reuses the same `compensation:` declarations |
| Journal replay | Engine checkpoint "time travel" (LangGraph) | Debugging one engine interactively |
| `string.Template` value paths | Jinja2 templates | Rich formatting needs; at the cost of a code surface |
| Workflow | Agent (Step 13) | The steps or their order cannot be known in advance |

## 10. Which approach fits which situation

- **Known steps, known order:** workflow (`pattern: workflow`).
- **Known steps with one judgement call:** workflow with a router or LLM node.
- **Writes to several external systems:** give every write a `compensation`.
- **Shared sub-process (KYC, approval chain):** a `subgraph` node.
- **Unknown steps:** an agent pattern (Step 13), possibly inside a workflow (`pattern: hybrid`).

## 11. Common mistakes

- Giving a compensation step edges of its own: it is run only by the saga (the validator says so).
- Expecting a failed step to be undone: only finished steps are.
- Building arguments with string concatenation in code instead of `$paths` in `config.args`.
- Using a `prompt://` ref before the prompt registry exists: use `config.template` for now.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| Branches run in parallel | ✅ | an LLM node (0.4 s) and a tool node run together, then join and route, on both engines; the workflow contract times parallel branches |
| A failed step triggers its compensation | ✅ | `ship` fails → `refund (undoing charge)`, then `release (undoing reserve)`, on both engines; a policy denial on a tool node also triggers compensation |
| Replay produces the same path | ✅ | a model-routed run replayed with a model that raises if called: identical values, one model request in total, on both engines |
| Function, LLM, tool, router nodes; conditional edges; joins; sub-workflows; timeouts | ✅ | `test_deterministic_workflows.py` (18 tests), `test_executors.py` |
| Tool nodes through the authorising executor with stable idempotency keys | ✅ | audit shows the decision; the key is `<run id>:count` |
| ADR | ✅ | ADR 0023 |

## 13. Recap

Step 12 made deterministic workflows complete. Specs can mix code, model, tool, router and
sub-workflow steps with parallel branches, joins and timeouts; tool steps run under policy with
idempotency keys that make retries safe. When a run fails, completed writes are undone newest
first, and every run is journalled so it can be replayed exactly without touching real systems.
All of it behaves the same on both runtime engines.
