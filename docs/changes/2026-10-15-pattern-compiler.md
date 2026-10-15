# Turn a spec into a runnable plan, and run it with a trace

**Step:** 04 · **Commit:** feat(compiler): compile specs to ExecutablePlans and run them traced · **Date:** 2026-10-15

## What changed
A new library, `agent-compiler`:

- `compile_spec(spec)` picks the pattern builder registered for `pattern:` and builds the plan.
  Graph patterns (`workflow`, `state_machine`, `hybrid`) are registered now; the others report
  which step brings them ("no pattern builder installed for 'react' (it arrives in Step 13)").
- Each `agent` node in a hybrid graph is expanded with its own inner pattern, namespaced
  (`resolve_exceptions.think`).
- Every tool node whose tool requires approval gets an **approval node in front of it**,
  automatically, so a spec cannot forget the gate.
- The plan carries metadata (risk tier, budgets, models, tools, guardrails) and a SHA-256
  `plan_hash` for lineage.
- `runtime_for(spec)` finds the runtime adapter by name; `just run <spec>` executes through it
  and sends an OpenTelemetry trace (`invoke_agent <name>` with a span per node) to the Collector.

## Why
The spec is the recipe card; the compiler is the head chef turning it into an ordered list of
stations and hand-offs that any kitchen (runtime adapter) can follow. Putting the approval gate
in at compile time is like the chef always adding "manager signs here" before the step that
spends money.

## How to check it
```
just run agents/hello-workflow/agent.yaml '{"name":"Ada"}'
```
prints `"status": "succeeded"`, `"greeting": "hello, Ada"` and a `trace_id`; with the stand-in
running, `curl 127.0.0.1:3200/api/v2/traces/<trace_id>` shows `invoke_agent hello-workflow`.

## Risks and follow-ups
Tracing setup lives in the compiler for now; Step 30 moves it into `agent-telemetry` with the
full GenAI conventions. Rejected approvals simply end the run until Step 28 adds outcomes.
