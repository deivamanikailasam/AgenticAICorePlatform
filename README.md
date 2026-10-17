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
| The big picture: architecture, decisions, plain English, techniques, strategy, when to choose what, laptop to production, lifecycle | [`docs/handbook/`](docs/handbook/README.md) |
| What has been measured | [`docs/results.md`](docs/results.md) |
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

All 40 steps across nine phases are built. [`PROGRESS.md`](PROGRESS.md) lists which acceptance
checks are verified here (against the Project 0 stand-in) and which still need a run against the
live Project 0 and real models (VERIFY-ON-MAC).
