# Step 14 · Planning and reflection

## 1. At a glance

| | |
|---|---|
| **Goal** | Agents that plan before acting, or check and improve their own work, within hard limits |
| **Time** | About two days |
| **You end up with** | `pattern: plan_execute`, `rewoo`, `reflection`, `evaluator_optimiser`; `planner.llm` and `critic.llm`; a harness that compares every pattern with ReAct on the same tasks |
| **New words** | plan-and-execute, re-plan, ReWOO, evidence reference, reflection, evaluator-optimiser, judge |

## 2. The big idea

ReAct (Step 13) thinks after every tool call. That is flexible but chatty: every step is another
model call. Sometimes it is cheaper to write the plan first and then just do it. Other times the
first answer is not good enough and a second look pays off. Step 14 adds both habits as patterns
a spec can choose, each with a hard limit on how many model calls it may make.

> *Analogy:* a builder can work out each next brick as they go (ReAct), draw the plan first and
> follow it, redrawing if a wall will not fit (plan-and-execute), hand a complete
> materials-and-steps sheet to the crew and only check the result at the end (ReWOO), reread
> their own work (reflection), or have an inspector mark it and fix what the inspector finds
> (evaluator-optimiser).

## 3. What we're doing

1. Add a model-backed planner and critic as swappable slots.
2. Build four strategies that run inside one agent node.
3. Cap every model call a pattern makes with the node's `max_iterations`.
4. Register each as a pattern builder so specs can name it.
5. Build a harness that runs every pattern on the same reference tasks and meters the cost.

## 4. Why we're doing this

The right pattern depends on the task and the model. Planning can save model calls when the
steps are predictable; review rounds can raise quality but cost more calls. Without a fair
comparison, the choice is guesswork, so the platform ships a bench that measures it on your own
model.

> *Watch out:* more thinking is not always better. Reflection with a weak model can talk itself
> out of a right answer, and a plan written up front cannot react to a surprise. Measure first.

## 5. Key ideas

- **Plan-and-execute.** The planner writes numbered steps (`E1`, `E2`, …). Each step is carried
  out by a small ReAct run that may only use that step's tool. If a step fails, the planner
  re-plans the rest (at most `max_replans`, default 2). One last call writes the answer.
- **ReWOO.** The planner writes the whole plan once, including tool arguments. Later arguments
  can point at earlier results: `#E1` (the whole result) or `#E1.words` (one field). The tools
  then run in order with no model calls in between, and one solver call writes the answer.
- **Reflection.** Draft, self-critique with the same model role, revise; at most `max_rounds`
  (default 2), or until the critique passes.
- **Evaluator-optimiser.** Draft, a separate evaluator (role `judge`) scores and gives feedback,
  improve; returns the best-scoring candidate when rounds run out.
- **One cap.** `max_iterations` counts every model call the node makes — planner, critic and
  sub-steps together. Reaching it stops the run (`stopped`), it does not fail it.
- **Same governance.** Tools still go through policy, rate limit and audit; tool results are
  passed back as data, not instructions.
- **Swappable roles.** `components: { planner: llm, critic: llm }` are the defaults; any planner
  or critic that passes its contract can replace them.

## 6. How to do it

A whole-spec ReWOO agent:

```yaml
autonomy: agent
pattern: rewoo
models: { default: local-qwen-fast }
budgets: { max_steps: 6, max_wall_seconds: 60, max_tokens: 20000, max_cost_aud: 1.0 }
tools:
  - ref: local://platform/calculator
  - ref: local://platform/word_count
```

An evaluator-optimiser node inside a hybrid workflow:

```yaml
- id: draft_reply
  type: agent
  pattern: evaluator_optimiser
  model: writer
  max_iterations: 6
  max_rounds: 2
  config:
    template: "Draft a reply to: ${input.message}"
```

Plan-and-execute with a cheaper model for the steps:

```yaml
pattern: plan_execute
models: { default: planner, executor: local-qwen-fast }
# on an agent node: executor_model: executor, max_replans: 1
```

Compare every pattern with ReAct on the reference tasks, with the real local model (Mac):

```bash
just compare-patterns
```

The table it prints names the dataset, its size, the model alias and the time of the run.

## 7. Benefits

- One line in the spec switches the pattern; the runtime and governance stay the same.
- Predictable tasks can take fewer model calls (ReWOO); quality-sensitive tasks get a review.
- A hard cap on model calls per node, whatever the pattern does inside.
- A fair, repeatable bench for choosing a pattern on your own model.

## 8. Drawbacks and risks

- ReWOO cannot change course if a tool returns something unexpected; it only reports it.
- Plan-and-execute adds a planning call and a final call around the steps.
- Reflection and evaluator-optimiser at least double the calls for a simple task.
- These patterns refuse tools that need approval (they cannot pause mid-plan); use `react`.
- Replay and checkpoints work per agent node, not per plan step, until Step 18.
- The reference set has 3 tasks: a smoke test, not a benchmark. No comparison numbers yet; they
  come from the Mac run.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Plan-and-execute | ReAct | Short tasks, or steps that depend heavily on what tools return |
| ReWOO | Plan-and-execute | Tool results may change the plan |
| Reflection (self) | Evaluator-optimiser (`judge`) | The drafting model is weak at judging its own work |
| `planner.llm` | A rules-based planner (your own component) | The steps are known; skip the model call |
| Strategies on one node | A workflow (Step 12) | The steps are fixed in advance and need per-step approvals or saga undo |

## 10. Which approach fits which situation

- **Two or three tool calls, open-ended:** `react`.
- **Several tool calls whose order is clear from the question:** `rewoo`.
- **Longer tasks where a step might fail:** `plan_execute`.
- **Writing where quality matters more than speed:** `evaluator_optimiser` with a `judge` role.
- **Quick polish of a short answer on a capable model:** `reflection`.
- **Anything that moves money or messages customers:** `react` with approvals, or a workflow.

## 11. Common mistakes

- Choosing a pattern from a blog post instead of running `just compare-patterns` on your model.
- Setting `max_iterations` too low for the pattern: plan-and-execute needs at least the plan
  call, one call per step and the final call.
- Giving the same weak model the `judge` role and expecting a strict review.
- Expecting ReWOO to recover from a bad plan: it will not re-plan; use `plan_execute`.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| Plan-and-execute with re-plan on failure | ✅ | a step failing beyond its repairs triggers one re-plan; the new plan completes; answer from step results |
| ReWOO: plan once, run tools without re-prompting | ✅ | exactly 2 model calls (plan, solve); both tools ran under policy with `#E1.words` substituted |
| Reflection / self-critique, bounded rounds | ✅ | critique uses the agent's own role; revision includes the feedback; stops when it passes |
| Evaluator-optimiser, bounded rounds | ✅ | critique uses `judge`; best-scoring candidate returned when rounds run out |
| Each a pattern builder; planner and critic swappable | ✅ | `pattern_builder.{plan_execute,rewoo,reflection,evaluator_optimiser}`; `planner.llm` and `critic.llm` pass their contracts |
| Each pattern runs a reference task and reports cost and steps vs ReAct | ✅ (mechanics) / ⚠️ **VERIFY-ON-MAC** (numbers) | all five patterns solve the 3 reference tasks on both adapters with a scripted stand-in model, every model and tool call metered; real numbers (dataset: `evals/reference/planning-tasks`, 3 tasks; model: the gateway's `default` alias) come from `just compare-patterns` on the Mac |
| Model-call cap covers the whole pattern | ✅ | reflection with `max_iterations: 3` stops after exactly 3 model calls |
| ADR | ✅ | ADR 0025 |

## 13. Recap

Step 14 gave agents two new habits: planning first (plan-and-execute, ReWOO) and checking their
work (reflection, evaluator-optimiser). Each is one spec line, runs inside one agent node on
either runtime, keeps the platform's policy and audit on every tool call, and is held to one cap
on model calls. Planner and critic are swappable slots, and a comparison bench measures each
pattern against ReAct on the same tasks so the choice rests on numbers from your own model.
