# Agentic AI Core Platform (Project A)

A reusable, enterprise-grade engine that turns one declarative file, `agent.yaml`, into a running,
governed agentic system: a deterministic workflow, a single tool-using agent, a team of agents, an
agentic RAG assistant, or a hybrid of these.

The platform supplies everything around the reasoning — tools and MCP, agent-to-agent (A2A), user
interface events (AG-UI), durable execution, memory, budgets, guardrails, human approval, identity,
observability, audit, governance, evaluation and red-teaming — so each new application only
describes *what* it should do.

It runs on top of **Project 0, the RAG Platform Foundation** (`~/dev/rag-platform`), and reuses its
shared services. It never re-implements or forks them.

| Where to start | Document |
|---|---|
| The specification (source of truth for scope) | [`docs/spec/Agentic_AI_Core_Platform_Build_Guide.md`](docs/spec/Agentic_AI_Core_Platform_Build_Guide.md) |
| Where the build is now | [`PROGRESS.md`](PROGRESS.md) |
| Run it on your Mac | [`docs/setup/local.md`](docs/setup/local.md) |
| Promote it to production | [`docs/setup/production.md`](docs/setup/production.md) |
| Learn it step by step | [`docs/guide/`](docs/guide/) |
| Why things are the way they are | [`docs/adr/`](docs/adr/) |
| What changed, in plain English | [`docs/changes/`](docs/changes/) |

## Quick start

```bash
just bootstrap   # install the pinned toolchain into .venv
just check       # lint, format, types, tests, policy tests, config checks
just health      # reach Project 0's shared services
just up          # start Project A services on Project 0's Docker network
```

## Status

Built in 40 steps across nine phases. See [`PROGRESS.md`](PROGRESS.md) for the current step and
for which acceptance checks are verified and which still need a run against a live Project 0.
