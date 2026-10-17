# Techniques

The techniques the platform uses, grouped by the problem each one solves. Each line names where
it lives and the step whose chapter explains it. "When to use which" is in
[when-to-choose.md](when-to-choose.md).

## Shaping the work: patterns

An `agent.yaml` names one of 18 patterns. The compiler turns it into the same kind of plan, so
every pattern gets the same controls.

| Pattern | What it does | Step |
|---|---|---|
| `workflow`, `state_machine` | Fixed steps decided by code; the model only fills in steps marked `llm` | 12 |
| `prompt_chain` | One model call feeds the next, with checks between | 15 |
| `router` | A cheap classification picks which path runs | 15 |
| `react` | The model thinks, calls a tool, reads the result, and repeats until done | 13 |
| `plan_execute`, `rewoo` | Plan all the steps first, then run them (`rewoo` plans without seeing tool results) | 14 |
| `reflection`, `evaluator_optimiser` | One pass writes, another critiques, and it revises until the critique passes | 14 |
| `agentic_rag` | The model decides what to retrieve, checks it is enough, and retrieves again if not | 16 |
| `supervisor`, `hierarchical` | A supervisor delegates to worker agents, which can lead their own teams | 22 |
| `handoff`, `group_chat`, `map_reduce`, `debate` | Pass the conversation on; take turns; split the work and merge it; argue both sides and judge | 23 |
| `hybrid` | Deterministic steps and agent steps in one plan, joined by edges with conditions (`on_ok`, `on_error`) | 15, 40 |
| `custom` | A team's own pattern builder, from its package (`components.pattern_builder`) | 39 |

A plan's nodes are `function`, `llm`, `tool`, `agent`, `router`, `approval`, `subgraph` and
`join`.
- A `subgraph` runs another spec as its own agent (Step 40).
- `approval` nodes are inserted by the compiler in front of every irreversible tool. A spec
  can't leave them out (Step 4).

## Keeping it on the rails

- **Budgets** cap steps, tokens, AUD, wall-clock time and delegation depth, for the whole run
  and for each worker (Step 26).
- **Loop detection by progress:** a run that repeats itself without new results is stopped
  (Step 26).
- **Circuit breakers** on failing tools and models are shared by every run in a process (Step 26).
- **Graceful stop:** at a limit, the run stops or summarises what it has, instead of failing
  silently (Step 26).

## Context and memory

- **Context engineering** (Step 21):
  - instructions, retrieved facts and history are assembled to a token budget;
  - old turns are summarised;
  - tools are loaded just in time, so only the ones a step needs are described to the model.
- **Checkpoints and threads:** every step is saved. A run resumes after a crash, and a thread can
  be forked from any checkpoint (time travel) (Step 18).
- **Durable runs on Temporal** for work that lasts hours or days (Step 19).
- **Long-term memory** per user and agent, with a write policy, expiry and erasure (Step 20).

## Safety

- **Untrusted content is data.** Tool results, retrieved documents and MCP tool descriptions are
  marked untrusted and screened. They never become instructions (Steps 7, 27).
- **Guardrails in parallel** at input, retrieved content, output, tool arguments and tool
  results: rules always on, a classifier and a model judge switchable, Presidio for personal
  data (Step 27).
- **Policy on every tool call** (OPA by default; Cerbos or OpenFGA as alternatives): tenant,
  clearance, side effect, risk tier, rate limit and kill switch (Step 10).
- **Approvals with evidence bound to the arguments:**
  - an approver can edit a call, and the approval covers exactly what will run;
  - the two-person rule, escalation and safe expiry (Step 28).
- **Least privilege by construction:**
  - each agent has its own Keycloak identity and only the tools its spec grants;
  - delegated tokens are down-scoped at each hop;
  - database credentials are issued per call and revoked after (Step 29).
- **Sandboxed code:** gVisor by default; Firecracker or Kata microVMs. No network, read-only
  root (Step 9).
- **Idempotency keys** on every write and irreversible tool, so a retry never pays twice (Step 6).

## Knowing what happened

- **GenAI spans** for every agent, model call and tool call, in one trace. Content goes only to
  Langfuse, redacted (Step 30).
- **A hash-chained audit trail:** any edit breaks the chain, and any run can be rebuilt from it
  and its checkpoints (Step 31).
- **Generated governance:** agent and system cards, lineage, the AI-use register, and
  CODEOWNERS by risk tier (Step 32).

## Knowing it works

- **Cassettes:** record a run's model and tool calls once, then replay them byte for byte with
  no network (Step 33).
- **MCP contract tests and chaos tests** through the real code paths (Step 33).
- **Deterministic checks over platform records:** which tools ran, with what arguments, which
  approvals were asked for. A judge model is used only where no check can decide, and only after
  people have calibrated it (Step 34).
- **Generated golden tasks**, at least 100 per app (Step 34).
- **Simulated users and environments** for multi-turn evaluation (Step 35).
- **The red team:** ASI01 to ASI10 attacks against a deliberately obedient model, so every
  control is tested as if the model were fooled. promptfoo and garak run against the real one
  (Step 35).
- **Eval gates per risk tier** on every pull request (Step 36).

## Shipping it

- **GitOps:** Kustomize overlays, Argo CD, and signed images pinned by digest (Step 38).
- **Run leases** across replicas: a stopped replica's runs are taken over (Step 38).
- **Releases:** a canary on 10% of threads (sticky per thread) or a shadow with no side effects;
  online evaluation and automatic rollback (Step 38).
- **Point-in-time recovery,** with a measured drill (Step 38).
- **The reuse kit:** a team's tools, patterns and evaluators plug in through entry points,
  inside the same controls (Step 39).
