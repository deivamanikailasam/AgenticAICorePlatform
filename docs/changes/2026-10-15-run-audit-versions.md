# The run API audits run start, plan, run end and the versions in force

**Step:** 31 · **Commit:** feat(agent-api): audit run, plan and versions · **Date:** 2026-10-15

## What changed
- **Events.** Each run writes:
  - `run.started`: the input's digest, plus whether this is a resume
  - `plan`: the plan hash and nodes, on the first start only
  - `run.ended`: status, stop reason, output digest, cost, tokens, model calls
  
  Failed and cancelled runs write `run.ended` too. All of these are written inside the run's `invoke_agent` span.
- **Stamping.** The assembly wraps its audit sink in `StampingAuditSink` and registers each agent's versions: spec version and hash, plan hash, prompt refs, the model alias per role, and tool versions. The approval desk writes through the same sink.
- **Team workers.** A worker's events now carry the worker's own spec name and version as `agent_id`, not the short worker name.

## Why
Step 31: "every event records who, what, which versions, and the trace id", and the trail brackets each run, so it can be read on its own.

## How to check it
`uv run pytest services/agent-api/tests/test_audit_trail.py`:
- An approved payment yields a trail that starts with `run.started`, `plan` and ends with `run.ended`. Every event validates against the catalogue, names the user and agent, and carries versions and a trace id that exists in the exported spans.
- A team run records three delegations, and worker tool calls appear under the parent run with the worker's own versions.

## Risks and follow-ups
Model versions are the gateway aliases (for example `local-qwen-fast`), not the underlying model build. LiteLLM resolves the alias, and its spend log records the concrete model.
