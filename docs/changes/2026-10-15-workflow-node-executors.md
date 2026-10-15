# Workflows can call models, route, use tools and run sub-workflows

**Step:** 12 · **Commit:** feat(agent-runtime): add LLM, router, tool and sub-workflow node executors · **Date:** 2026-10-15

## What changed
- **LLM nodes** render `config.template` with run values (`$input.text`, `${validate.total}`)
  and call the platform model client by role; `config.output_schema` asks for structured output.
  Values are inserted as text only, never evaluated; `prompt://` registry refs give a clear
  "arrives in Step 24" error until the prompt registry exists.
- **Router nodes** pick one of their declared `routes`, by a function or by the model with a
  schema that only allows those routes; anything else fails the node.
- **Tool nodes** call their tool through the authorising executor (policy, rate limit, audit)
  with arguments mapped from run values (`config.args`). The idempotency key is
  `<run id>:<node id>`, so retries, resumes and replays can never repeat a side effect.
- **Sub-workflow nodes** compile the referenced spec and run it on the same adapter as a child
  run (`parent_run_id` set).
- `agent_compiler.run` is now the composition root that plugs these in; `agent_tools.assemble`
  turns the plan's tool list into registry names and grants (rate limits). Plan metadata now
  carries each tool's approvers and rate limit.
- New end-to-end tests compile `agent.yaml` specs and run them on **both** adapters: an LLM node
  and a tool node running in parallel, then routing; the saga undoing completed steps; a policy
  denial on a tool node triggering compensation; replay taking the same path without calling the
  model; a sub-workflow; a node timeout taking the error route.

## Why
Most business processes are fixed steps with a few judgement calls: check the invoice (code),
summarise it (model), count or look something up (tool), decide the route, call a shared
sub-process. Each step type now works the same way on either engine, under the same policy.

## How to check it
`uv run pytest libs/agent-compiler/tests/test_deterministic_workflows.py libs/agent-runtime/tests/test_executors.py`
(18 + 7 tests).

## Risks and follow-ups
Approvals that *policy* requires on a tool node still stop the call until Step 28 passes
approval evidence to policy (fail closed). The CLI `run` needs a tool executor for specs with
tool nodes; the agent API (Step 17) wires one automatically.
