# Step 22 · Supervisor and hierarchical teams

## 1. At a glance

| | |
|---|---|
| **Goal** | Teams of agents where a supervisor hands out the work and each worker keeps its own tools, permissions and budget |
| **Time** | About two days |
| **You end up with** | `pattern: supervisor` / `hierarchical` with a `supervisor:` section; typed shared state with per-worker `reads` and `writes`; LLM and rules dispatchers; policy on every delegation; team budgets, circuit breakers and nesting limits; per-agent cost in the trace; the `research-report` reference app |
| **New words** | supervisor, worker, shared state, delegation, circuit breaker |

## 2. The big idea

Some jobs are easier split up: one agent finds sources, another works out the numbers, a third
writes the report. A supervisor agent decides who does what next, passes each worker only what
it needs, takes back only what that worker is allowed to produce, and decides when the job is
done. Each worker is a separate agent with its own toolbox. The writer cannot use the
researcher's search tool, any more than the researcher can approve a payment.

> *Analogy:* an editor with three freelancers. The editor briefs each one with only the
> material they need, accepts only the piece they were hired for, keeps an eye on the total
> budget, and stops sending work to someone who keeps missing deadlines.

## 3. What we're doing

1. Declare a team in `agent.yaml`: workers, typed shared state, the dispatcher, limits.
2. Run each worker as its own agent, with its own tools, grants, model roles and budget.
3. Pass workers only the state they read; keep only the fields they write, validated.
4. Ask policy before every delegation, and audit it.
5. Cap the team's total spend, stop using failing workers, and limit nesting.
6. Show each agent's cost in the trace.
7. Build the research-report reference app.

## 4. Why we're doing this

A single agent with every tool is a single point of failure: one confusing document, and it can
use any of its powers. Splitting the work also splits the permissions. Teams bring their own
risks, though: a worker's output can try to steer the supervisor (OWASP ASI07), and one failing
worker can drive up retries and cost (ASI08). Both are handled here, and each has a test.

> *Watch out:* a worker's output is data from another agent, not an instruction. The supervisor
> sees it wrapped as untrusted, and anything a worker returns beyond its own fields is dropped.

## 5. Key ideas

- **Workers are separate agents.** Each is its own `agent.yaml`, run as a child run with only
  its own tools and grants.
- **Typed shared state.** Fields have JSON Schemas. A worker `reads` some fields and `writes`
  others. It is not started before its reads exist, and its writes are validated.
- **Dispatchers.**
  - `llm`: the supervisor's model chooses the next worker and task, constrained to the team's
    names or `FINISH`.
  - `rules`: no model; the first worker that still has something to write and has what it
    needs.
- **Policy on delegation.** OPA decides `agent.delegate`: only the team's workers, and never
  one whose kill switch is on. Each decision is audited.
- **Limits.**
  - The team budget covers every worker's spend.
  - A worker that fails twice is taken out (circuit breaker).
  - Rounds and nesting depth are capped.
  - A team that ends without its answer stops rather than succeeding.
- **Per-agent cost.** Each worker run is its own span in the team's trace, with AUD cost and
  tokens.

## 6. How to do it

```yaml
autonomy: multi_agent
pattern: supervisor
agents:
  - { name: researcher, spec: workers/researcher.yaml, role: "Finds sources", reads: [task], writes: [sources] }
  - { name: analyst, spec: workers/analyst.yaml, role: "Computes figures", reads: [task, sources], writes: [figures] }
  - { name: writer, spec: workers/writer.yaml, role: "Writes the report", reads: [task, sources, figures], writes: [report] }
supervisor:
  dispatcher: llm        # or rules
  model: planner
  max_rounds: 6
  max_depth: 3
  max_failures: 2
  output: report
  state:
    sources: { type: array, minItems: 1 }
    figures: { type: object }
    report: { type: string, minLength: 20 }
```

Start a run like any agent:

```bash
curl -s -X POST http://127.0.0.1:8091/v1/agents/research-report/runs -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"input": {"task": "How fast did rooftop solar grow in Victoria?"}}'
```

The result holds `answer`, `state`, `rounds` (who did what, and how it ended), `cost_by_agent`
and `stop_reason`.

## 7. Benefits

- Each worker can only use its own tools, checked twice (agent loop and policy).
- Workers see less data, so leaks and prompt injection reach less.
- Spend is capped for the whole team, and failures do not cascade.
- The trace shows what each agent cost.
- The rules dispatcher runs predictable teams with no supervisor model at all.

## 8. Drawbacks and risks

- More model calls than a single agent: the supervisor decides each round.
- Worker runs are not checkpointed separately; a crash repeats the current worker.
- The dispatcher sees each state value truncated to 4,000 characters.
- Cerbos covers tool calls only; delegation policy needs OPA for now.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| LLM dispatcher | Rules dispatcher | The order is fixed (research, then analysis, then writing): cheaper and predictable |
| Supervisor team | One agent with all tools | Small tasks with low risk and few tools |
| Supervisor team | Handoff or group chat (Step 23) | Control should pass between peers rather than through a coordinator |

## 10. Which approach fits which situation

- **Report from research plus figures:** a supervisor with researcher, analyst and writer, and
  the rules dispatcher once the order is stable.
- **Open-ended investigations:** the LLM dispatcher with a tight `max_rounds` and team budget.
- **Large organisations:** hierarchical teams, where a department supervisor is a worker of a
  top supervisor, with `max_depth` set.
- **Sensitive tools:** give them to one worker only, with approvals on that worker's spec.

## 11. Common mistakes

- Giving every worker every tool, which defeats the point of a team.
- Letting a worker write a field the supervisor depends on (for example the task).
- Leaving `output` unset, so a failed team looks finished.
- Unbounded rounds or budget on an LLM dispatcher.
- Treating a worker's text as instructions to the supervisor.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| The research-report reference app runs with three workers | ✅ | researcher → analyst → writer, all rounds `ok`, report produced; also through the run API with each worker assembled with only its own tool |
| A worker's tool cannot be called by another worker | ✅ | the writer's call to `search` is refused by its agent loop (unknown tool), and by policy when the tool was wrongly registered for it ("not granted"); the search tool never ran |
| Per-agent cost shows in the trace | ✅ | spans `invoke_agent research-report-researcher`, `-analyst` and `-writer` in one trace, each with `agent_platform.cost.aud` (AUD 0.002 in the test) and token counts; `cost_by_agent` in the result |
| Hierarchies nest supervisors | ✅ | a nested team stops at `max_depth` |
| Shared state explicit and typed; workers get only what they need | ✅ | the researcher's prompt has no sources; the writer's has sources and figures; fields not in its `writes` are dropped |
| LLM supervisor; rules dispatcher alternative | ✅ | both run the reference app |
| ASI07 (inter-agent) and ASI08 (cascading failures) tests | ✅ | OPA on delegation (3 Rego tests, unknown or denied workers refused, decisions audited); worker output delimited as data; team budget cap; circuit breaker; nesting limit |
| ADR | ✅ | ADR 0033 |

## 13. Recap

Step 22 made teams. A supervisor hands work to worker agents, each its own spec with its own
tools, grants and budget. Workers see only the state they need and can change only their own
fields. Every delegation passes policy and is audited, worker output is treated as data, and
budgets, circuit breakers and nesting limits stop failures from spreading. The research-report
app runs this way, and the trace shows what each agent cost.
