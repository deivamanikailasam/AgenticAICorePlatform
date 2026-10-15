<!-- Exported from the Claude Doc "Agentic AI Core Platform — Build Guide" (https://claude.ai/artifact/3LWPs8E78NdLfS68NHR8iu), revision 21, on 2026-10-15. The two embedded diagrams (architecture, roadmap) are interactive widgets and are re-drawn as Mermaid in docs/architecture/. This file is the source of truth for scope; do not edit it by hand — re-export when the doc changes. -->

# Agentic AI Core Platform — Build Guide

2026-10-15 · Deiva mani Kailasam

## How to use this guide

This guide specifies Project A, the **Agentic AI Core Platform**: a reusable, enterprise-grade engine that any workflow, agent, multi-agent, agentic RAG or hybrid application plugs into. It is built in 40 small steps on the same Mac and the same shared services as Project 0, the RAG Platform Foundation.

In plain English: Project 0 built the building's electricity, plumbing, security desk and CCTV. Project A builds the **operations centre** inside it: the dispatch desk that assigns work to staff (agents), the tool cage that controls who may use which tool, the approvals desk for risky actions, and the logbook that proves afterwards who did what and why.

### Scope rule: reuse, don't rebuild

Project A **must not re-implement anything Project 0 already provides**. It consumes these as shared services:

| Project 0 service | How Project A uses it |
|---|---|
| LiteLLM gateway, budgets, fallbacks (Steps 7–10) | The only path to every model; per-agent and per-run budget keys |
| Postgres + pgvector, Qdrant, OpenSearch, SeaweedFS, Valkey (11–15) | Checkpoints, long-term memory, artefacts, queues, locks, caches |
| Keycloak, JWT auth, OPA / OpenFGA (16–18) | User identity, agent service accounts, tool and data authorisation |
| OpenTelemetry, Prometheus, Grafana, Loki, Tempo, Langfuse (19–22) | Agent traces using the GenAI conventions, SLOs, dashboards |
| Hash-chained audit log, prompt registry, cards and lineage (23–25) | Extended with agent decisions, tool calls and approvals |
| Eval harness, CI and supply chain, OpenBao/Vault, Kubernetes + Argo CD (26–29) | Agent evals and gates, secrets for tools, deployment |
| `rag-core` and its switchable RAG architectures (30–35) | Exposed to agents as a governed retrieval tool (MCP) |

### Every step follows the same chapter template

Claude Code writes one chapter per step in `docs/guide/`, with the same 13 sections as the Project 0 guide, in this order:

1. **At a glance**: goal, time, what you end up with, new words.
2. **The big idea**: the step in everyday language, with an analogy.
3. **What we're doing**: the concrete tasks.
4. **Why we're doing this**: the problem solved, and why enterprises care.
5. **Key ideas**: concepts, one at a time.
6. **How to do it**: numbered instructions with every command and file.
7. **Benefits**.
8. **Drawbacks and risks**.
9. **Alternatives**: other tools and approaches, and when each wins.
10. **Which approach fits which situation**.
11. **Common mistakes**.
12. **Done when**: a checklist; never move on until it's all ticked.
13. **Recap**: the whole step in one paragraph.

The same coloured callouts are used: *In plain English*, *Analogy*, *Why this matters*, *Tip*, *Watch out* and *In a company*.

### How this document relates to the code

This document is the **specification**. It fixes the scope, the order, the defaults, the switchable alternatives and the acceptance criteria for each step. The repository holds the full chapters, code, configs, tests and ADRs that implement it. Where this spec and the code disagree, the code is wrong until an ADR says otherwise.

### Versions in this guide

Standards versions were checked on 15 October 2026 (see References). Before each step, Claude Code re-checks the current release of every tool and protocol it installs and records the pinned version in that step's ADR. Treat any version here as "current at writing", not as permanent.

## Platform at a glance

Project A turns one declarative file, `agent.yaml`, into a running, governed agentic system. The same file can describe a deterministic workflow, a single tool-using agent, a team of agents, an agentic RAG assistant, or a hybrid of these. The platform supplies everything around the reasoning: tools, memory, durability, safety, approvals, tracing, audit and evaluation.

In plain English: an agent is an AI model that can decide its next step and use tools to do it. A workflow is a fixed recipe. Real business systems need both, plus brakes, supervision and a paper trail. Project A provides those once, so each new application only describes *what* it should do.

### The autonomy spectrum it supports

| Pattern | Who decides the next step | Typical use |
|---|---|---|
| Deterministic workflow (DAG or state machine) | The code | Invoice validation, ETL with one LLM step |
| Prompt chain and router | The code, with an LLM classifier | Triage, intent routing |
| Single tool-calling agent (ReAct loop) | The model, inside guard rails | Support assistant, research helper |
| Plan-and-execute, ReWOO, reflection | The model plans, then executes | Multi-step analysis and reports |
| Agentic RAG | The model decides when and how to retrieve | Knowledge assistant over company documents |
| Supervisor or hierarchical multi-agent | A coordinating agent | Report generation with specialist workers |
| Handoff (swarm), group chat, debate | Agents pass control between each other | Customer journeys across departments |
| Parallel fan-out and map-reduce | The code splits, agents work in parallel | Review 200 contracts against a checklist |
| Hybrid | A workflow with agentic nodes and approval gates | Most real enterprise processes |

### What each layer does

| Layer | What it does | Everyday comparison | Steps |
|---|---|---|---|
| Spec and compiler | Reads `agent.yaml`, validates it, compiles it into a runnable graph | The recipe card and the head chef who reads it | 1–4 |
| Model access | Every model call goes through the Project 0 gateway, per agent role | The switchboard | 5 |
| Tools and MCP | Registered, typed, permissioned tools; MCP in and out; a sandbox for code | The locked tool cage with a sign-out sheet | 6–10 |
| Orchestration runtime | Runs every pattern behind one adapter interface | The dispatch desk | 11–17 |
| State, memory, durability | Checkpoints, resumable long runs, long-term memory, context management | The case file that survives a power cut | 18–21 |
| Multi-agent and interop | Team patterns, A2A to agents elsewhere, an agent registry | The staff directory and the internal phone system | 22–25 |
| Safety and security | Budgets, loop limits, guardrails, human approvals, agent identity | Brakes, speed limits and a supervisor's signature | 26–29 |
| Observability, audit, governance | GenAI traces, SLOs, a tamper-evident record, agent and tool cards | CCTV, the official logbook, the compliance binder | 30–32 |
| Evaluation and testing | Replay tests, trajectory evals, simulations, red-teaming, CI gates | Inspections and driving tests | 33–36 |
| Production and reuse | Threat model, environments, canaries, DR, template, SDK, reference apps | The franchise manual | 37–40 |

### The protocol stack

Three open protocols cover the three boundaries an agent has:

- **MCP (agent ↔ tools and data).** The current stable revision is [2026-07-28](https://github.com/modelcontextprotocol/specification/releases/tag/2026-07-28). It made the protocol stateless: each request carries its own protocol version and capabilities, which suits horizontally scaled servers ([overview](https://scalar.com/learn/mcp/what-is-mcp)).
- **A2A (agent ↔ agent).** Stable v1.0 shipped in March 2026, with later 1.x releases. It is a Linux Foundation project and complements MCP rather than replacing it ([Linux Foundation](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year?hsLang=en)).
- **AG-UI (agent ↔ user interface).** An open, event-based protocol for streaming agent progress, tool calls, shared state and approval requests to a front end ([overview](https://promptmetheus.com/resources/llm-knowledge-base/agent-user-interaction-protocol-ag-ui)).

### Architecture diagram

[embedded diagram: platform architecture · 5 layers on Project 0 (node/168f54d9-75b1)]

Requests enter through the API or a gateway, the compiler and runtime execute the spec, the four platform services act on every step, and all of it runs on Project 0's shared services.

## Design principles and founding ADRs

Ten principles decide every trade-off in this platform. When a step is unclear, the principle wins.

1. **Least autonomy that does the job.** Default to a deterministic workflow; add agentic decisions only where they earn their cost and risk. Every `agent.yaml` declares its autonomy level.
2. **Wrap, don't rebuild, the orchestrator.** Control flow is a solved problem. The platform's value is the governed layer around it, behind a runtime adapter so the engine can be swapped.
3. **Standards at every boundary.** MCP for tools, A2A for agents, AG-UI for user interfaces, OpenTelemetry GenAI conventions for traces, OIDC/OAuth for identity.
4. **Everything is switchable by configuration.** Each component sits behind a contract and is chosen in `agent.yaml` or `platform.yaml`. No application code changes to switch.
5. **Safe by default.** Budgets, loop limits, tool allowlists, guardrails, audit and tracing are on unless an ADR turns them off.
6. **Treat model output and tool output as untrusted data.** Never as instructions. This is the core defence against indirect prompt injection.
7. **Every side effect is classified, authorised and idempotent.** Tools declare whether they read, write or do something irreversible; irreversible actions require approval.
8. **Durable by design.** Any run longer than a request can crash, pause for a human or wait days, and resumes without repeating completed side effects.
9. **Measured, not assumed.** No pattern, prompt or model change merges without passing the eval gates.
10. **Explained for humans.** Every change is a small Conventional Commit with a plain-English explanation in the docs.

### Founding ADRs (written in Step 1)

| ADR | Decision | Main alternative rejected | Why |
|---|---|---|---|
| 0001 | Project A is a separate uv-workspace repo that consumes Project 0 services; it never forks them | Extend the RAG monorepo | Clear ownership; RAG and agents release separately; Project 0 stays the shared base |
| 0002 | Python 3.12, uv workspace, FastAPI, Pydantic v2 | TypeScript stack | Same toolchain as Project 0; best AI library coverage |
| 0003 | Declarative `agent.yaml` (JSON-Schema validated) compiled by a pattern compiler | Code-only agent definitions | Reviewable, diffable, governable; one spec, many patterns |
| 0004 | Runtime adapter interface; LangGraph 1.x is the default adapter | Write our own graph engine | LangGraph 1.0 stabilised durable state, persistence and human-in-the-loop interrupts ([changelog](https://changelog.langchain.com/announcements/ann_lxOZRCoFw2Qn9)); an adapter avoids lock-in |
| 0005 | Two durability tiers: LangGraph Postgres checkpointer for runs under ~15 minutes; Temporal for long-running or multi-day runs | Temporal for everything | Temporal adds operational weight; most runs don't need it |
| 0006 | MCP (2026-07-28) is the only tool integration protocol; in-process tools are wrapped in the same contract | Framework-native tools | Portability; one permission and audit path |
| 0007 | A2A v1.x for cross-boundary agent calls; in-process agents talk through the runtime | A2A everywhere | Network hops and auth overhead aren't worth it inside one process |
| 0008 | All model calls go through the Project 0 LiteLLM gateway | Direct provider SDKs | Budgets, fallbacks, accounting and one audit path |
| 0009 | OPA decides every tool call and data access; OpenFGA for relationship-based checks | Checks inside tool code | Policy as code, testable, auditable, consistent |
| 0010 | OpenTelemetry GenAI semantic conventions for all agent telemetry | Vendor-specific tracing | Portable across Langfuse, Phoenix, Tempo and others |

Every later step that makes a hard-to-reverse choice adds its own ADR (`just adr "<title>"`), following the Project 0 template and lifecycle: an accepted ADR is never edited, only superseded.

## Repository layout, tech stack and conventions

The repo is `~/dev/agent-platform`, a uv workspace monorepo with the same conventions as `~/dev/rag-platform`: signed Conventional Commits, trunk-based development, a `justfile` menu, pre-commit hooks, SOPS-encrypted dev secrets and ADRs.

```text
~/dev/agent-platform/
├── libs/
│   ├── agent-contracts/   ← interfaces + Pydantic models: Agent, Tool, Memory, Checkpointer, Guardrail… (Step 2)
│   ├── agent-spec/        ← agent.yaml JSON Schema, loader, validator, versioning (Step 3)
│   ├── agent-compiler/    ← pattern compiler: spec → runnable graph (Step 4)
│   ├── agent-runtime/     ← runtime adapters: langgraph (default), others (Step 11)
│   ├── agent-patterns/    ← workflow, react, plan-execute, router, supervisor, swarm… (Steps 12–16, 22–23)
│   ├── agent-tools/       ← tool registry, MCP client/server, sandbox, tool policy (Steps 6–10)
│   ├── agent-memory/      ← checkpoints, long-term memory, context engineering (Steps 18–21)
│   ├── agent-safety/      ← budgets, loop control, guardrails, HITL (Steps 26–28)
│   ├── agent-telemetry/   ← OTel GenAI spans, metrics, audit emitter (Steps 30–31)
│   ├── agent-evals/       ← trajectory/task evaluators, simulators, red-team suites (Steps 34–35)
│   └── agent-sdk/         ← public SDK for application teams (Step 39)
├── services/
│   ├── agent-api/         ← FastAPI: runs, threads, approvals, SSE/AG-UI stream (Step 17)
│   ├── agent-worker/      ← executes runs from the queue; Temporal worker (Step 19)
│   ├── mcp-gateway/       ← platform MCP server + proxy to external MCP servers (Steps 7–8)
│   ├── a2a-gateway/       ← A2A server/client, Agent Cards (Step 24)
│   └── sandbox-runner/    ← isolated code execution (Step 9)
├── agents/                ← agent.yaml specs: reference apps and examples
├── templates/             ← copier template for new agent projects (Step 39)
├── policies/              ← OPA Rego: tool, data, approval and budget policies + tests
├── prompts/               ← prompt registry entries (Project 0 format)
├── cards/                 ← agent, tool and system cards + lineage
├── evals/                 ← golden tasks, scenarios, red-team suites, gates.yaml, baselines
├── infra/compose/         ← services added on top of Project 0's stack (joins its network)
├── deploy/                ← Kustomize base + envs (dev, staging, prod), Argo CD apps
├── docs/
│   ├── guide/             ← one chapter per step (the 13-section template)
│   ├── adr/  architecture/  runbooks/  security/  changes/
├── tests/                 ← cross-service integration and end-to-end tests
├── pyproject.toml  uv.lock  justfile  .pre-commit-config.yaml  .sops.yaml
```

### Default stack

| Concern | Default | Notes |
|---|---|---|
| Language and tooling | Python 3.12, uv, Ruff, mypy (strict on libs), pytest | Same as Project 0 |
| API | FastAPI, Pydantic v2, SSE streaming | AG-UI events over SSE |
| Orchestration | LangGraph 1.x via the runtime adapter | Alternatives in the catalogue |
| Durable long runs | Temporal (local dev server, then cluster) | Postgres checkpointer for short runs |
| Queue and locks | Valkey (Project 0) | Run queue, idempotency keys, rate limits |
| State and memory | Postgres + pgvector (Project 0) | Separate `agent` database and schemas |
| Artefacts | SeaweedFS S3 (Project 0) | Large tool outputs, files, transcripts |
| Tools | MCP 2026-07-28 Python SDK | Keep compatibility with 2025-11-25 servers |
| Sandbox | gVisor-isolated containers locally | Firecracker or a managed sandbox in production |
| Policy | OPA (+ OpenFGA) from Project 0 | Rego tests in CI |
| Telemetry | OpenTelemetry SDK → Project 0 Collector → Tempo, Langfuse | GenAI semantic conventions |
| Evals | Project 0 harness + agent evaluators; promptfoo and garak for red-teaming | DeepEval and Inspect as options |
| Models | The two local Qwen models via LiteLLM; cloud models as fallbacks | Model per role: planner, executor, judge, router |

### Conventions Claude Code must follow

- **One logical change per commit**, Conventional Commit format, signed, authored by the repository owner (see the prompt section).
- **Every commit ships its docs**: code, tests, the step chapter section it touches, and a plain-English entry in `docs/changes/` explaining what changed and why.
- **No secrets in git.** Dev secrets live in SOPS files; runtime secrets come from OpenBao/Vault.
- **Ports bind to 127.0.0.1**, data lives in named volumes, and images are pinned by digest, exactly as in Project 0.
- **Contract tests first.** Every new component implementation must pass the shared contract test suite for its interface before it is registered.

## Roadmap

The 40 steps run in order across nine phases; each phase builds on the previous one, and no step starts until the one before it meets its Done-when checklist.

[embedded diagram: build roadmap · 9 phases, 40 steps (node/07ecebd5-100e)]

Phase I ends with three reference apps that prove the platform runs a workflow, a multi-agent team and an agentic RAG assistant from specs alone.

## Phase A — Foundation, contracts and the agent spec (Steps 1–4)

Phase A produces the skeleton every other step plugs into: a repo connected to Project 0, the contracts, the `agent.yaml` spec and a compiler that turns a spec into a runnable graph.

| Step | What you build | Done when |
|---|---|---|
| **1 · Repo, workspace and link to Project 0** | uv workspace, hygiene files, `justfile`, pre-commit, SOPS, ADRs 0001–0010. `infra/compose/compose.yaml` joins Project 0's Docker network as `external`. New `agent` database, Keycloak client `agent-platform` and LiteLLM virtual keys created by idempotent scripts. | `just check` passes; `just up` starts Project A services against a running Project 0; a health script reaches the gateway, Postgres, Valkey, Keycloak, OPA and the OTel Collector |
| **2 · Contracts and the component registry** | Typed interfaces in `agent-contracts` for every switchable part (list below). A registry with `@register("tool_executor", "mcp")`-style entries, discovered through Python entry points. A shared contract test suite per interface. | Two dummy implementations of each interface pass the contract tests; `just components` lists them |
| **3 · The ****agent.yaml**** spec** | JSON Schema (generated from Pydantic), a loader with `${env}` and secret references, semantic validation (unknown tools, cycles, missing budgets), `spec_version` and migrations. | Invalid specs fail with readable errors; `just validate agents/` passes; schema published in `docs/reference/` |
| **4 · Pattern compiler and runtime adapter skeleton** | `compile(spec) → ExecutablePlan` with a pattern plug-in per `pattern:` value; a `RuntimeAdapter` interface (`build`, `invoke`, `stream`, `resume`, `cancel`, `get_state`). | A "hello" workflow spec compiles and runs end to end through the stub adapter, with a trace in Tempo |

### The contracts (switchable interfaces)

| Interface | Responsibility |
|---|---|
| `ModelClient` | Chat, tool-calling, structured output and streaming through the gateway |
| `Tool`, `ToolExecutor`, `ToolRegistry` | Typed tool definition, invocation (local, MCP, A2A, sandbox), discovery |
| `PatternBuilder` | Turns one pattern section of a spec into graph nodes and edges |
| `RuntimeAdapter` | Runs the compiled graph on an engine |
| `Checkpointer`, `DurableExecutor` | Saves and restores run state; long-running execution |
| `MemoryStore`, `MemoryPolicy`, `ContextManager` | Long-term memory; what to remember; what goes into the context window |
| `Guardrail`, `PolicyDecisionPoint`, `ApprovalGate` | Checks before and after model and tool calls; authorisation; human approvals |
| `BudgetController`, `LoopDetector` | Token, step, cost and time limits; runaway detection |
| `Planner`, `Router`, `Critic` | Reusable reasoning roles used by patterns |
| `TelemetryEmitter`, `AuditSink` | GenAI spans and metrics; hash-chained audit events |
| `Evaluator`, `Simulator` | Scores runs and trajectories; generates synthetic users and tasks |

### A minimal `agent.yaml`

```yaml
spec_version: 1
name: invoice-triage
version: 0.3.0
owner: finance-platform
risk_tier: medium            # low | medium | high → drives approvals and eval gates
autonomy: hybrid             # workflow | agent | multi_agent | hybrid
pattern: hybrid
models:
  default: local-qwen-fast   # LiteLLM alias
  planner: local-qwen-quality
runtime: { adapter: langgraph, durability: checkpoint }   # or: temporal
budgets: { max_steps: 25, max_tokens: 60000, max_cost_aud: 0.50, max_wall_seconds: 300 }
tools:
  - ref: mcp://erp/get_invoice          # read
  - ref: mcp://erp/post_payment         # irreversible → approval required
    approval: { required: true, approvers: group:finance-approvers }
  - ref: mcp://rag-core/search          # Project 0 retrieval as a tool
memory: { short_term: checkpoint, long_term: { store: pgvector, scope: tenant } }
guardrails: [prompt_injection, pii_redaction, tool_args_schema, output_schema]
graph:
  - { id: validate, type: function, fn: invoice.validate }
  - { id: resolve_exceptions, type: agent, pattern: react, tools: [get_invoice, search] }
  - { id: pay, type: tool, tool: post_payment }
  edges: [validate -> resolve_exceptions: on_error, validate -> pay: on_ok, resolve_exceptions -> pay]
evals: { suite: evals/invoice-triage, gates: default }
```

The exact schema is defined in Step 3; this example shows the intent: patterns, models, tools, budgets, approvals, memory, guardrails and evals all live in one reviewable file.

## Phase B — Models, tools and MCP (Steps 5–10)

Phase B gives agents hands: model access per role, a typed and permissioned tool layer, MCP in both directions and a sandbox for code. In plain English, it is the tool cage: every tool is labelled, has a sign-out sheet, and some tools need a supervisor's signature.

| Step | What you build | Default → switchable | Done when |
|---|---|---|---|
| **5 · Model access per role** | `ModelClient` over the Project 0 gateway: tool calling, JSON-schema structured output, streaming, retries. Role aliases (`planner`, `executor`, `router`, `judge`, `summariser`) mapped in `platform.yaml`. A capability check refuses a model that lacks tool calling for a tool-using node. | LiteLLM gateway → direct provider SDK only behind an ADR | Each role resolves to a model; a structured-output test passes on both local Qwen models; per-run token and AUD cost recorded |
| **6 · Tool contract and registry** | `Tool` model: name, version, JSON Schema in/out, `side_effect: read \| write \| irreversible`, `idempotent`, timeout, retry policy, data classification, owner. Registry with versioning and deprecation. Output size cap and spill to S3. | In-process registry → registry backed by Postgres | Schema-invalid arguments are rejected before execution; a duplicate idempotency key returns the first result; tool cards generated |
| **7 · MCP client** | Connect to external MCP servers over Streamable HTTP and stdio. Support the stateless 2026-07-28 revision and negotiate down to 2025-11-25 for older servers. Tool discovery, name-spacing (`mcp://server/tool`), OAuth for remote servers, allowlisted servers only. Tool annotations are treated as untrusted unless the server is trusted. | MCP Python SDK → another compliant SDK | Two external servers (one local, one remote) are discovered, called, traced and audited; an unlisted server is refused |
| **8 · Platform MCP server** | `mcp-gateway` exposes platform tools and Project 0 `rag-core` search as MCP tools, with Keycloak auth and OPA checks on every call. Also proxies external servers, so all tool traffic has one policy and audit point. | Gateway pattern → direct client-to-server for trusted internal tools | An external MCP client (e.g. Claude Code or Inspector) can call `rag-core/search` with a user token and only sees permitted documents |
| **9 · Sandboxed code execution** | `sandbox-runner`: run model-written code in a disposable, network-off, CPU/memory/time-capped container with gVisor; files in and out through S3; no host mounts. | gVisor containers → Firecracker microVMs, Kata, or a managed sandbox service | A fork bomb, a network call and a 10-minute loop are all contained and reported; results come back as artefacts |
| **10 · Tool authorisation** | OPA policies decide each call from: user, tenant, agent, tool, side-effect class, data classification, arguments and risk tier. On-behalf-of tokens via OAuth token exchange so tools act with the *user's* rights, never broader. Per-tool rate limits in Valkey. | OPA → Cerbos; OpenFGA for relationships | Rego unit tests cover allow, deny and approval-required; a tool called for another tenant's data is denied and audited |

### Tool side-effect classes

| Class | Example | Default rule |
|---|---|---|
| `read` | Search documents, get an order | Allowed if the user may read the data |
| `write` (reversible) | Draft an email, create a ticket | Allowed with idempotency key; logged |
| `irreversible` | Send a payment, delete records, email a customer | Human approval required unless policy says otherwise; never retried automatically |

**Watch out:** the most common agent security failure is a tool that runs with a powerful service account. Step 10 makes "acts with the user's own rights" the default, which is also what the OWASP agentic guidance recommends for tool misuse and privilege abuse.

## Phase C — Orchestration runtime and patterns (Steps 11–17)

Phase C makes every single-agent and workflow pattern available from `pattern:` in a spec, all on one runtime adapter. Multi-agent patterns follow in Phase E.

| Step | What you build | Default → switchable | Done when |
|---|---|---|---|
| **11 · Runtime adapter** | `LangGraphAdapter` implementing `RuntimeAdapter`: compiled plan → `StateGraph`, typed state with reducers, interrupts, streaming modes, cancellation, recursion limit tied to the budget. Second adapter stub to prove the seam. | LangGraph 1.x → Microsoft Agent Framework 1.0, OpenAI Agents SDK, Pydantic AI, Google ADK | The same spec passes the same tests on LangGraph and on the second adapter for the workflow pattern |
| **12 · Deterministic workflows** | DAG and state-machine patterns: function nodes, LLM nodes, tool nodes, conditional edges, parallel branches, joins, sub-workflows, timeouts, compensation steps (saga) for failed writes. | — | Branches run in parallel; a failed step triggers its compensation; replay produces the same path |
| **13 · Single tool-calling agent** | ReAct-style loop: model → tool calls (parallel when allowed) → observation → model, until a final structured answer, a budget limit or an approval interrupt. Final-answer schema validation with a repair retry. | Native tool-calling → text ReAct parser for models without it | Agent solves the golden tool tasks; malformed tool calls are repaired or fail cleanly; loop stops at `max_steps` |
| **14 · Planning and reflection** | Plan-and-execute (re-plan on failure), ReWOO (plan once, run tools without re-prompting), reflection/self-critique, and evaluator-optimiser loops with a bounded number of rounds. | Each is a `PatternBuilder`; `Planner` and `Critic` roles are swappable | Each pattern runs a reference task and reports cost and steps vs ReAct on the same task |
| **15 · Routing, chaining and hybrid** | Prompt chains with validation gates between steps; LLM or classifier routers; hybrid graphs where workflow nodes and agent nodes mix freely, as in the `agent.yaml` example. | LLM router → embedding classifier or rules | Routing accuracy is measured on a labelled set; the hybrid reference app runs end to end |
| **16 · Agentic RAG** | Agent that decides whether, what and how to retrieve using `rag-core` through MCP: query decomposition, multi-hop retrieval, self-check of grounding, corrective re-retrieval, citation pass-through. Reuses Project 0 retrieval and guardrails instead of reimplementing them. | `rag-core` architectures selectable per call (naive, corrective, adaptive, graph, SQL) | Multi-hop golden questions beat Project 0's naive RAG on the same set; every answer keeps citations and access control |
| **17 · Run API and streaming** | `agent-api`: create run, stream events, get state, cancel, resume, list threads, submit approval. Events emitted as AG-UI (text deltas, tool call start/end, state deltas, approval requests) over SSE. Idempotent run creation. | SSE → WebSocket | A minimal web page shows live tool calls and an approval button using only AG-UI events |

### Choosing a pattern

| If the task… | Use | Why |
|---|---|---|
| has known steps and needs audit | Workflow | Cheapest, testable, explainable |
| needs a classification then a fixed path | Router + workflow | One cheap model decision |
| needs a few tools in an unknown order | Single agent | Flexible, still bounded |
| is long and multi-step with dependencies | Plan-and-execute or ReWOO | Fewer model calls than open-ended ReAct |
| needs quality review of its own output | Evaluator-optimiser | Measurable improvement, bounded rounds |
| needs company knowledge with citations | Agentic RAG | Reuses governed retrieval |
| spans several specialities or teams | Supervisor or handoff (Phase E) | Separation of concerns and permissions |

Tip: start every new application as a workflow with one agent node, and promote to more autonomy only when evals show the gain is worth the cost.

## Phase D — State, memory, context and durable execution (Steps 18–21)

Phase D makes runs survive crashes, pauses and long waits, and gives agents memory without letting memory become a security hole. In plain English: a case file that survives a power cut, plus a filing system for what the agent learned, with rules about what it may write down.

| Step | What you build | Default → switchable | Done when |
|---|---|---|---|
| **18 · Checkpoints and threads** | Postgres checkpointer in the `agent` database: a snapshot after every node, keyed by tenant, thread and run. Time travel (inspect and fork from an earlier checkpoint), retention by policy, encryption of sensitive fields. | Postgres → Valkey (dev only) or SQLite (tests) | Kill the worker mid-run; the run resumes from the last checkpoint without repeating a completed write tool |
| **19 · Durable long runs (Temporal)** | `durability: temporal` runs the agent as a Temporal workflow: model and tool calls become activities with retry policies; durable timers and signals for multi-day waits and approvals; heartbeats for long tools; large payloads stored in S3 by reference, because Temporal caps payload size. | Temporal → Restate or DBOS; LangGraph-only for short runs | A run waits 24 hours (simulated) for an approval, survives a worker and server restart, then finishes; the history shows no duplicate side effects |
| **20 · Long-term memory** | Memory types: semantic (facts), episodic (past runs and outcomes), procedural (learned instructions, versioned like prompts). Scoped per user, tenant or agent. A `MemoryPolicy` controls writes: PII redaction, a provenance tag, confidence, TTL, and no writes from untrusted tool output without validation. Right-to-erasure hooks reuse Project 0's lifecycle. | pgvector store → Qdrant, Mem0, Letta, Zep | Memory improves a repeat-task eval; a poisoned document cannot write a persistent instruction; erasure removes a user's memories everywhere |
| **21 · Context engineering** | `ContextManager`: token budgeting per section (system, tools, memory, history, retrieved data), history trimming and rolling summaries, tool-result compaction, just-in-time tool loading for large tool sets, prompt-cache-friendly ordering, and clear delimiting of untrusted content. | Summarise → trim → offload to S3 strategies, selectable | A 200-turn conversation stays under the context limit with no drop in task success on the long-dialogue eval |

### Durability tiers

| Tier | Use when | Cost |
|---|---|---|
| In-memory | Unit tests only | None; lost on crash |
| Postgres checkpointer | Interactive runs up to minutes; human approval within the session | Low; reuses Project 0 Postgres |
| Temporal | Runs of hours or days; external signals; strict exactly-once side effects | An extra service to run and monitor |

**Watch out:** durability does not make side effects safe on its own. A resumed run can repeat a payment unless the tool is idempotent (Step 6) or wrapped as a Temporal activity with a stable idempotency key. Both are required for irreversible tools.

**Why this matters for memory:** a memory store is a persistent prompt-injection channel. One malicious document summarised into memory can steer every future run. Step 20 treats memory writes like database writes from an untrusted source: validated, attributed and reversible.

## Phase E — Multi-agent systems and interoperability (Steps 22–25)

Phase E adds teams of agents, inside one process and across organisational boundaries. Each agent keeps its own tools, permissions and budget, so a compromised or confused agent can't use another agent's powers.

| Step | What you build | Default → switchable | Done when |
|---|---|---|---|
| **22 · Supervisor and hierarchical teams** | A supervisor agent delegates to worker agents (each a sub-graph with its own spec, tools and budget), collects results and decides when the task is done. Hierarchies nest supervisors. Shared state is explicit and typed; workers receive only what they need. | LLM supervisor → rules-based dispatcher for predictable tasks | The research-report reference app runs with three workers; a worker's tool cannot be called by another worker; per-agent cost shows in the trace |
| **23 · Handoff, group chat, parallel and debate** | Handoff (swarm): an agent transfers control and context to a specialist. Group chat with a speaker-selection policy. Parallel fan-out with map-reduce over many items, with concurrency limits. Debate or critic panels with a judge. Termination conditions on every pattern. | Each pattern is a `PatternBuilder` | Each pattern has a reference spec and eval; no pattern can run past its budget or termination condition |
| **24 · A2A server and client** | `a2a-gateway`: publish chosen agents as A2A agents with signed Agent Cards (skills, auth requirements, endpoints); call remote A2A agents as tools; task lifecycle, streaming and push notifications; Keycloak-issued credentials; OPA checks on outbound and inbound calls. Messages from remote agents are untrusted input. | A2A v1.x (JSON-RPC or HTTP+JSON binding) → gRPC binding | An agent built with a different framework calls a Project A agent over A2A and vice versa; an unsigned or unlisted remote agent is refused |
| **25 · Agent registry and discovery** | Catalogue of every agent and its version, owner, risk tier, tools, autonomy, eval status and card; lifecycle states (draft → approved → deprecated → retired). Supervisors can only delegate to approved agents. | Postgres registry → an external catalogue | `just agents` lists all agents with status; a supervisor cannot delegate to a draft agent in staging or prod |

### Multi-agent rules the platform enforces

- **Least privilege per agent.** Tools and data access are granted to an agent, not to the team.
- **Bounded delegation.** Maximum delegation depth and fan-out are set in the spec and enforced at runtime.
- **Budgets roll up.** Each agent has its own budget; the run total can never exceed the parent's budget.
- **Untrusted messages.** Output from one agent is data to the next, never instructions to bypass its policy. This addresses insecure inter-agent communication and cascading failures from the OWASP agentic list.
- **One trace.** All agents in a run share one trace, with an `invoke_agent` span per agent.

**In a company:** multi-agent is often chosen for the wrong reason. It multiplies cost and failure points. The platform's evals compare a multi-agent design against a single agent with the same tools, and the ADR for each application must justify the extra agents with measured gains.

## Phase F — Safety, guardrails, human approval and identity (Steps 26–29)

Phase F puts brakes, speed limits and supervision on every agent. These controls are on by default in every spec; turning one off needs an ADR and a higher eval bar.

| Step | What you build | Default → switchable | Done when |
|---|---|---|---|
| **26 · Budgets and loop control** | `BudgetController` enforcing per-run and per-agent limits: steps, tokens, AUD cost, wall-clock time, tool calls per tool, delegation depth. Hierarchical budgets mapped to LiteLLM virtual keys. `LoopDetector`: repeated identical tool calls, no-progress cycles, oscillating plans. Circuit breakers on failing tools and models. Graceful stop with a partial result and reason. | Hard stop → stop + summarise | A deliberately looping agent stops within its limits; the run record shows which limit tripped; cost dashboards match the gateway's accounting |
| **27 · Guardrails** | Checks before and after each model and tool call: prompt-injection detection on user input *and* on retrieved and tool content; PII detection and redaction (Project 0 Presidio); tool-argument schema and value checks (e.g. amount limits); output schema and policy checks; topic and safety filters; secret and credential leak detection on outputs. Guardrails run in parallel where possible to limit latency. | Classifier-based injection detector → LLM judge or rules; each guardrail switchable | The guardrail eval suite passes, including indirect injection via a document and via an MCP tool description |
| **28 · Human-in-the-loop** | `ApprovalGate`: pause before risky actions with approve, reject, edit-arguments and escalate; approvals routed by OPA to the right group with SLA timers; reviewers see the proposed action, reason, data and risk; decisions are audited. Also review-after (sampled QA) and take-over patterns. | LangGraph interrupts (short) → Temporal signals (long) | An irreversible tool always waits for approval; an edited approval runs with the edited arguments; an expired approval cancels safely |
| **29 · Agent identity and secrets** | Each agent has a Keycloak service identity; user requests carry delegated, down-scoped tokens (OAuth token exchange); tools get short-lived credentials from OpenBao/Vault at call time, never from the prompt or environment; credentials never enter model context. Optional workload identity (SPIFFE/SPIRE) in Kubernetes. | Keycloak + OpenBao → cloud IAM and secret managers | A tool call's audit record names user, agent and credential lease; a leaked lease expires within minutes; no secret appears in any trace or prompt |

### Risk tiers drive the defaults

| Risk tier | Example | Approvals | Eval gate | Monitoring |
|---|---|---|---|---|
| Low | Internal FAQ helper, read-only tools | None | Standard | Sampled review |
| Medium | Creates tickets, drafts customer emails | Before external-facing writes | Standard + red-team suite | Online evals on a sample |
| High | Payments, records changes, regulated advice | Before every irreversible action; two-person rule optional | Full suite, higher thresholds, human sign-off | Online evals on all runs, alerting |

**Watch out:** guardrails reduce risk but never remove it. Prompt-injection detection is probabilistic. The real protection is architectural: least-privilege tools, approvals on irreversible actions, and treating content as data. Guardrails are the seatbelt, not the brakes.

## Phase G — Observability, audit and governance (Steps 30–32)

Phase G makes every run explainable after the fact: what the agent saw, decided and did, at what cost, under which versions, and who approved it. It extends Project 0's observability and audit rather than adding new stacks.

| Step | What you build | Done when |
|---|---|---|
| **30 · Agent telemetry, metrics and SLOs** | Spans follow the OpenTelemetry GenAI conventions: `invoke_agent {agent}` per agent, `chat` per model call, `execute_tool {tool}` per tool call, with token usage, model, tool and agent attributes ([spec](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans)); MCP spans use the MCP conventions. Prompt and response content goes to Langfuse only, redacted and access-controlled, never to general logs. Metrics: runs, success, steps per run, tool error rate, approvals pending, cost per run and per tenant, p50/p95 latency, budget trips, guardrail hits. Grafana dashboards, SLOs, alert rules and runbooks. | One trace shows a multi-agent run end to end in Tempo and Langfuse; alerts fire on a test budget-trip spike and link to a runbook |
| **31 · Agent audit trail** | New event types in the Project 0 hash-chained audit log: run started/ended, plan, each tool call (arguments hashed, sensitive values redacted), policy decision, guardrail result, approval request and decision, memory write, delegation, A2A call. Each event records user, agent, versions (spec, prompt, model, tool) and the trace ID. | The audit verifier proves the chain intact; a run can be reconstructed from audit plus checkpoints; tampering with one event is detected |
| **32 · Governance artefacts** | Agent cards and tool cards (purpose, owner, risk tier, autonomy, tools, data, limitations, eval results), generated from specs and registries; system card per reference app; lineage of spec → prompts → models → tools → datasets; change approval rules in CODEOWNERS by risk tier; an AI-use register entry per agent. | Cards regenerate in CI and fail the build if stale; a high-risk spec change cannot merge without the named approver |

### Example agent trace

```text
invoke_agent research-supervisor            run=7f2c  tenant=acme  cost=$0.21 AUD
├── chat local-qwen-quality (plan)          in=1.8k out=0.4k
├── invoke_agent web-researcher
│   ├── execute_tool mcp://search/web       240 ms
│   └── chat local-qwen-fast (summarise)
├── invoke_agent rag-analyst
│   └── execute_tool mcp://rag-core/search  citations=6  denied_docs=2
├── guardrail output_schema                 pass
└── approval publish_report                 approved by j.smith after 4m12s
```

**In a company:** auditors and incident responders ask four questions: who asked, what the agent did, why it was allowed, and who approved it. Steps 30–32 answer each one from records rather than from someone's memory.

## Phase H — Evaluation and testing (Steps 33–36)

Phase H proves the platform and every agent built on it work, and keeps them working. Agents fail semantically, often with no error, so tests check behaviour and outcomes, not just status codes.

| Step | What you build | Done when |
|---|---|---|
| **33 · Testing pyramid** | Unit tests with a scripted fake model; contract tests per interface; record-and-replay of model and tool calls (cassettes) for deterministic integration tests; tool contract tests against MCP servers; end-to-end tests on the compose stack; chaos tests (kill worker, slow tool, model timeout, gateway down). | `just test` runs in under 5 minutes with no network; replayed runs are byte-identical; chaos tests show correct resume and fallbacks |
| **34 · Agent evaluation harness** | Golden task sets per reference app with expected outcomes. Metrics: task success, final-answer correctness, trajectory quality (expected tools, order, unnecessary steps), tool-call accuracy (right tool, valid arguments), policy compliance, steps, tokens, AUD cost, latency. Deterministic checkers first; LLM-as-judge only with written rubrics and calibration against human labels. Results stored with versions for comparison. | Every reference app has at least 100 tasks; judge agreement with human labels is measured and reported; a baseline is committed |
| **35 · Simulation and red-teaming** | User simulators (persona, goal, patience) for multi-turn tasks; environment simulators with mock tools and seeded faults; red-team suites mapped to the OWASP Top 10 for Agentic Applications (ASI01–ASI10): goal hijack, tool misuse, privilege abuse, memory poisoning, unexpected code execution, inter-agent attacks, cascading failures, rogue agents and more. promptfoo and garak run the attack libraries. | Each ASI category has at least one automated test; findings are tracked as issues; high-severity findings block release |
| **36 · CI eval gates** | `evals/gates.yaml` per risk tier: minimum task success, maximum regression vs baseline, zero critical red-team failures, cost and latency ceilings. Runs on every PR that touches specs, prompts, tools, models or patterns; full suites nightly; results posted to the PR. | A PR that lowers task success beyond the threshold is blocked; a cost increase above the ceiling needs explicit approval |

### What gets measured

| Level | Question | Example metric |
|---|---|---|
| Outcome | Did it achieve the goal? | Task success rate on golden tasks |
| Trajectory | Did it get there sensibly? | Tool-call precision and recall; redundant steps |
| Safety | Did it stay inside policy? | Policy violations, guardrail misses, red-team pass rate |
| Efficiency | At what cost? | Steps, tokens, AUD per task, p95 latency |
| Robustness | Does it survive faults? | Success rate under injected tool failures |
| Human | Do people trust it? | Approval override rate, user feedback |

**Watch out:** an eval set the agent was tuned on stops measuring generalisation. Keep a held-out set that is never used for prompt tuning, and refresh it with real failures from production. Report the set size with every number, e.g. "0.91 task success on 150 held-out tasks".

## Phase I — Production, promotion and the reuse kit (Steps 37–40)

Phase I takes the platform from the Mac to staging and production, then packages it so a new agentic application starts in minutes.

| Step | What you build | Done when |
|---|---|---|
| **37 · Threat model and compliance** | STRIDE threat model per component plus the OWASP agentic mapping (see "Threat model and compliance mapping"); data-flow diagrams; control mapping to NIST AI RMF, ISO/IEC 42001, the EU AI Act and the Australian Privacy Principles; data lifecycle for runs, memory and transcripts (retention, erasure, export). | Every threat has an owner, a control and a test; the control matrix links to evidence in the repo |
| **38 · Environments and promotion** | Kustomize overlays for dev, staging and prod; Argo CD apps; promotion by Git PR with eval gates and approvals; versioned specs deployed as config; canary and shadow runs for new agent versions; online evaluation on sampled production runs; feature flags per agent; worker autoscaling on queue depth; HA for API, workers, Temporal and MCP gateway; backups and DR drills for checkpoints, memory and audit. | A new agent version goes dev → staging → 10% canary → prod by PR only; a failed online-eval gate rolls back automatically; a DR drill restores state within the stated RPO/RTO |
| **39 · Reuse kit** | `copier` template that creates a new agent project (spec, evals, cards, CI, deploy overlay) wired to the platform; `agent-sdk` (Python) for custom tools, patterns and evaluators; a CLI (`agentctl validate / run / eval / promote`); the platform's own MCP and A2A endpoints documented for other teams. | A new project created from the template passes CI and runs its first agent in under 30 minutes |
| **40 · Reference apps and wrap-up** | Three reference applications proving the "any pattern" claim: (1) **Agentic RAG knowledge assistant** on `rag-core`; (2) **multi-agent research and report generator** (supervisor, workers, critic, approval before publishing); (3) **hybrid invoice-triage workflow** (deterministic validation, agentic exception handling, approval before payment). Final catalogue, runbooks, architecture docs, measured results table, and a retrospective ADR. | All three apps pass their gates; the results table holds only measured numbers with set sizes; the guide's roadmap shows 40 of 40 |

### Cloud mapping (for promotion beyond the Mac)

| Local component | AWS | Azure | Google Cloud |
|---|---|---|---|
| Kubernetes (OrbStack) + Argo CD | EKS | AKS | GKE |
| Postgres + pgvector | RDS / Aurora PostgreSQL | Azure Database for PostgreSQL | Cloud SQL / AlloyDB |
| Valkey | ElastiCache (Valkey) | Azure Cache for Redis | Memorystore |
| SeaweedFS S3 | S3 | Blob Storage | Cloud Storage |
| Temporal (self-hosted) | Temporal Cloud or self-hosted on EKS | Same | Same |
| Keycloak | Cognito or keep Keycloak | Entra ID | Identity Platform |
| OpenBao / Vault | Secrets Manager or Vault | Key Vault | Secret Manager |
| Local Qwen via mlx-lm | Bedrock or GPU nodes with vLLM | Azure AI Foundry or GPU nodes | Vertex AI or GPU nodes |
| Sandbox (gVisor) | Firecracker-based isolation or a managed sandbox | Container Apps dynamic sessions or a managed sandbox | gVisor on GKE Sandbox |

These mappings are starting points; verify current managed-service names and features before committing an ADR, as they change frequently.

## Threat model and compliance mapping

The platform's threat model uses STRIDE per component plus the [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications/), published December 2025, as the agent-specific checklist. Every row below must have an automated test in the Step 35 red-team suite.

| OWASP ID | Risk | Platform controls | Steps |
|---|---|---|---|
| ASI01 | Agent goal hijack | Untrusted-content delimiting; injection detection on retrieved and tool content; tool allowlists per agent; approvals on sensitive actions | 21, 27, 28 |
| ASI02 | Tool misuse and exploitation | Typed tool schemas, argument value checks, side-effect classes, OPA per call, rate limits | 6, 10, 27 |
| ASI03 | Identity and privilege abuse | Per-agent identity, on-behalf-of down-scoped tokens, short-lived credentials from OpenBao, access reviews | 10, 29 |
| ASI04 | Agentic supply chain | Allowlisted MCP servers and A2A agents; signed Agent Cards; pinned and scanned images and packages; tool and agent inventory (AIBOM) from the registries | 7, 24, 25, 32 |
| ASI05 | Unexpected code execution | gVisor sandbox, no network by default, resource caps, no host mounts | 9 |
| ASI06 | Memory and context poisoning | Memory write policy, provenance tags, validation, TTLs, inspect-and-flush tooling | 20, 21 |
| ASI07 | Insecure inter-agent communication | Mutual authentication, signed cards, OPA on delegation, messages treated as data | 22–24 |
| ASI08 | Cascading failures | Budgets that roll up, delegation limits, circuit breakers, environment separation | 26, 38 |
| ASI09 | Human-agent trust exploitation | Approval screens show the raw action and arguments, not just the agent's summary; what was shown is audited | 28, 31 |
| ASI10 | Rogue agents | Behavioural metrics and alerts, registry lifecycle with owner and expiry, a tested kill switch per agent and per tenant | 25, 30, 38 |

The risk names follow the OWASP list as summarised in [this walkthrough](https://cycode.com/blog/owasp-top-10-agentic-applications/). Also review the OWASP LLM Top 10 and the OWASP MCP Top 10 for model- and tool-layer risks.

### Kill switches

Three levels, each tested in a drill: stop one run; disable one agent version (registry flag, enforced by the API and workers); disable all agent tool execution for a tenant (OPA data flag). Each takes effect within one policy refresh and is audited.

### Compliance control mapping

| Framework | What it asks for | Where the evidence lives |
|---|---|---|
| NIST AI RMF (Govern, Map, Measure, Manage) | Risk management across the AI lifecycle | Risk tiers, threat model, eval results, incident runbooks |
| ISO/IEC 42001 | An AI management system: policy, roles, risk, monitoring, improvement | ADRs, cards, CODEOWNERS approvals, SLOs, retrospectives |
| EU AI Act | Risk classification; for high-risk uses: logging, human oversight, transparency, accuracy and robustness | Risk tier per agent, audit log, approval gates, system cards, eval gates |
| Australian Privacy Principles (Privacy Act 1988) | Collection, use, disclosure, security, access and correction of personal information | PII redaction, memory scoping and erasure, data lifecycle, access logs |
| Australian Government AI guidance (voluntary) | Guardrails such as accountability, testing, human oversight, transparency and record-keeping | Same artefacts; verify the current version of the guidance before mapping, as it was being updated during 2025–26 |

This mapping supports, but does not replace, legal and compliance review for any real deployment.

## Local setup and promotion to production

Project A runs on the same MacBook Pro as Project 0. Models stay native on macOS for the Metal GPU; every Project A service runs in containers on Project 0's network. The repo must ship two detailed documents: `docs/setup/local.md` and `docs/setup/production.md`. This section fixes what they must cover.

### Local setup (what `docs/setup/local.md` must walk through)

1. **Prerequisites.** Project 0 running and healthy (`just up && just check` in `~/dev/rag-platform`); Brewfile additions for Project A (`temporal` CLI, `copier`, `opa`, `promptfoo` via its installer) recorded first, then installed.
2. **Clone and sync.** `git clone`, `just bootstrap`, `just sync`, `direnv allow`; SOPS decryption of `secrets/dev.enc.env` with the same age key as Project 0.
3. **Provision shared resources** (idempotent scripts): `agent` database and roles in Postgres, Keycloak client and agent service accounts, LiteLLM virtual keys per agent role with budgets, OpenBao paths and policies for tool credentials, OPA bundle for Project A policies, Langfuse project.
4. **Start services.** `just up` starts `agent-api`, `agent-worker`, `mcp-gateway`, `a2a-gateway`, `sandbox-runner` and a Temporal dev server, all with ports bound to 127.0.0.1, memory caps, health checks and log rotation.
5. **Smoke tests.** `just smoke` runs: a workflow spec, a ReAct agent with an MCP tool, an approval round trip, an agentic RAG question against `rag-core`, and an A2A call; then checks traces in Tempo and Langfuse and events in the audit log.
6. **Memory budget.** Re-measure Mac memory with both Qwen models loaded plus the new containers; raise the OrbStack VM cap only if `docker stats` shows pressure, and record the change in an ADR.
7. **Troubleshooting.** A table of common failures: gateway unreachable via `host.docker.internal`, Keycloak token audience mismatch, OPA denies everything, Temporal namespace missing, sandbox fails without gVisor, MCP version negotiation errors.

### Promotion to production (what `docs/setup/production.md` must walk through)

1. **Local Kubernetes first.** Deploy to OrbStack Kubernetes with Kustomize and Argo CD exactly as Project 0 did; models stay native and are reached through the gateway.
2. **Environments.** `dev`, `staging`, `prod` overlays; separate namespaces, databases, Keycloak realms or clients, OpenBao paths and LiteLLM keys; no shared credentials across environments.
3. **Release flow.** PR → CI (lint, types, unit, contract, replay tests, Rego tests, SBOM, image scan and signing, eval gates) → merge → Argo CD syncs `dev` → promotion PR to `staging` with full evals and red-team suite → canary in `prod` (10% traffic or shadow mode for new agent versions) → online-eval check → full rollout. Automatic rollback on SLO or online-eval breach.
4. **Agent versioning.** Specs, prompts, tools and models are versioned independently; a run records all four; in-flight runs finish on the version they started with (Temporal worker versioning for durable runs).
5. **Scaling and HA.** API stateless behind a load balancer; workers scale on queue depth; MCP gateway and A2A gateway replicated; Temporal cluster or Temporal Cloud; Postgres HA with point-in-time recovery; Valkey with replicas.
6. **Security hardening.** Network policies (deny by default, egress allowlist per tool), Pod Security Standards restricted, mTLS between services, workload identity, secrets only from OpenBao/Vault, sandbox on a dedicated node pool.
7. **Operations.** SLOs and alerts, on-call runbooks per alert, kill-switch drills, quarterly access reviews of agent permissions, DR drills with stated RPO and RTO, cost reviews per tenant and per agent.
8. **Cloud move.** Use the cloud mapping in Phase I; replace local Qwen with a managed or GPU-hosted model behind the same LiteLLM aliases so specs don't change.

**Done when (whole platform):** a teammate on a fresh Mac with Project 0 running reaches a green `just smoke` by following `local.md` only, and a new agent version reaches prod through canary using `production.md` only.

## Documentation, commit and change standards

Every change must be understandable by a non-specialist reading the docs a year later. These rules are acceptance criteria, not suggestions.

### Documentation set the repo must contain

| Document | Location | Purpose |
|---|---|---|
| Step chapters (13-section template) | `docs/guide/step-NN-*.md` | The learning guide, one chapter per step |
| Architecture | `docs/architecture/` | C4 context, container and component diagrams (Mermaid), data flows, sequence diagrams for a run, an approval and an A2A call |
| ADRs | `docs/adr/` | Every hard-to-reverse decision, with alternatives and consequences |
| Plain-English change log | `docs/changes/YYYY-MM-DD-<slug>.md` | One per commit: what changed, why, how to verify, in everyday language |
| Reference | `docs/reference/` | `agent.yaml` schema, `platform.yaml`, API (OpenAPI), events (AG-UI), CLI, SDK |
| Component catalogue | `docs/reference/components.md` | Generated from the registry: every switchable part, its options and when to choose each |
| Setup | `docs/setup/local.md`, `docs/setup/production.md` | As specified above |
| Runbooks | `docs/runbooks/` | One per alert, plus kill-switch and DR drills |
| Security | `docs/security/` | Threat model, OWASP mapping, control matrix, data lifecycle |
| Cards | `cards/`, rendered to `docs/cards/` | Agent, tool and system cards, lineage |
| How-to guides | `docs/how-to/` | Add a tool, add an MCP server, add a pattern, add a guardrail, write evals, promote an agent |
| Glossary | `docs/glossary.md` | Every term, in plain English |

### Commit rules

- **One logical change per commit.** A commit never mixes unrelated changes. A step is usually 5–15 commits.
- **Conventional Commits** with a scope: `feat(tools): add side-effect classes to tool contract`.
- **Each commit contains:** the code, its tests, the doc section it affects and its `docs/changes/` entry. CI must pass on every commit, not only on the last commit of a branch.
- **Commit body explains in plain English** what changed and why, then lists how it was verified.
- **Authorship:** commits are authored and signed by the repository owner using the git identity already configured on the machine. No AI tool attribution lines or co-author trailers are added.
- **Branch per step:** `feat/step-NN-<slug>`, merged by PR with squash or rebase per the repo's ruleset, and a tag `step-NN` after merge.

### Plain-English change entry template

```markdown
# <Title in everyday words>

**Step:** NN · **Commit:** <type(scope): subject> · **Date:** YYYY-MM-DD

## What changed
One short paragraph a non-engineer can follow.

## Why
The problem this solves, with an analogy if it helps.

## How to check it
The exact command and the expected output.

## Risks and follow-ups
What could go wrong; anything deliberately left for later.
```

### Definition of done for every step

- [ ] Code, tests and contract tests pass locally and in CI
- [ ] Step chapter written with all 13 sections
- [ ] ADR added for any hard-to-reverse choice, with alternatives
- [ ] Change entries written for every commit
- [ ] Catalogue, reference docs and cards regenerated
- [ ] Traces, metrics and audit events present for the new behaviour
- [ ] Evals updated and gates passing; no unmeasured claims in docs
- [ ] Roadmap status updated in the guide

## Catalogue of switchable components

Each slot below is an interface from Step 2; the default is built first, and each alternative is a registered implementation passing the same contract tests. Alternatives marked *(stub)* ship as documented adapters with a contract test and an ADR on when to complete them, so the catalogue stays honest about what is fully implemented.

| Slot | Default | Alternatives |
|---|---|---|
| Runtime adapter | LangGraph 1.x | [Microsoft Agent Framework 1.0](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/), OpenAI Agents SDK, Pydantic AI, Google ADK, CrewAI *(stub)* |
| Durable executor | Postgres checkpointer | Temporal ([AI integrations](https://docs.temporal.io/ai)), Restate *(stub)*, DBOS *(stub)* |
| Patterns | Workflow (DAG, state machine), ReAct | Prompt chain, router, plan-and-execute, ReWOO, reflection, evaluator-optimiser, agentic RAG, supervisor, hierarchical, handoff/swarm, group chat, map-reduce, debate |
| Model client | LiteLLM gateway | Direct provider SDK (ADR required) |
| Model per role | Local Qwen fast and quality aliases | Any gateway alias: cloud models, judge models, small routers |
| Tool transport | MCP Streamable HTTP | MCP stdio, in-process, A2A remote agent as tool, sandboxed code |
| Tool registry | Postgres-backed | In-memory (tests) |
| Code sandbox | gVisor containers | Firecracker, Kata Containers, managed sandbox service |
| Policy decision point | OPA | Cerbos; OpenFGA for relationships |
| Checkpointer | Postgres | SQLite (tests), Valkey (dev) |
| Long-term memory | pgvector | Qdrant, Mem0, Letta, Zep |
| Memory policy | Validate + redact + provenance | Read-only memory, human-curated memory |
| Context strategy | Rolling summary + tool-result compaction | Trim-only, offload to S3, just-in-time tool loading |
| Injection detector | Classifier model | LLM judge, rules, none (low tier with ADR) |
| PII guardrail | Presidio (Project 0) | Cloud DLP services |
| Approval channel | API + AG-UI web page | Slack or Teams approvals, email, ticketing system |
| Agent-to-agent | In-process via runtime | A2A JSON-RPC, A2A HTTP+JSON, A2A gRPC |
| UI events | AG-UI over SSE | AG-UI over WebSocket, plain SSE |
| Telemetry backend | OTel → Tempo + Langfuse | Arize Phoenix, other OTLP backends |
| Eval runners | Platform harness | DeepEval, Inspect AI, promptfoo, garak (red-team) |
| Judge | Rubric-based LLM judge | Deterministic checkers, human review queue |
| Secrets | OpenBao | HashiCorp Vault, cloud secret managers |
| Deployment | Kustomize + Argo CD | Helm, Flux |

**Tip:** the catalogue in the repo is generated from the registry, so it can't drift. This table is the build target; the generated one is the truth.

## Glossary and references

### Glossary

| Term | Plain-English meaning |
|---|---|
| Agent | An AI model that decides its next step and uses tools to act, inside limits |
| Workflow | A fixed sequence of steps decided by code, possibly with AI steps inside |
| Autonomy level | How much the model decides versus the code |
| Tool | A function the agent can call, with a typed input and output |
| Side-effect class | Whether a tool only reads, changes something reversibly, or does something that can't be undone |
| MCP | Model Context Protocol: the standard plug for connecting agents to tools and data |
| A2A | Agent-to-Agent protocol: the standard way agents from different systems talk |
| AG-UI | Agent-User Interaction protocol: the standard event stream from an agent to a user interface |
| Agent Card | A published description of an A2A agent: skills, endpoint, auth |
| Checkpoint | A saved snapshot of a run's state so it can resume after a crash or pause |
| Durable execution | Running long processes so they survive failures and waits without repeating finished work |
| Idempotency key | A unique ID that makes repeating a request safe: the second attempt returns the first result |
| Human-in-the-loop (HITL) | A person approves, edits or rejects an agent's action before it happens |
| Guardrail | An automatic check before or after a model or tool call |
| Prompt injection | Hidden instructions in text that try to make the model ignore its rules; *indirect* when they arrive through documents or tool results |
| Trajectory | The sequence of steps and tool calls an agent took to reach its answer |
| Context engineering | Deciding exactly what goes into the model's limited context window |
| Supervisor | An agent that splits work and delegates to worker agents |
| Handoff | One agent passing control and context to another |
| Kill switch | A tested way to stop a run, an agent version or a tenant's agents immediately |
| AIBOM | AI bill of materials: inventory of agents, models, tools and MCP servers |
| Canary | Sending a small share of traffic to a new version before everyone |

### References (checked 15 October 2026)

- [MCP specification release 2026-07-28](https://github.com/modelcontextprotocol/specification/releases/tag/2026-07-28) and [what changed in it](https://scalar.com/learn/mcp/what-is-mcp)
- [A2A one-year milestones and v1.0 (Linux Foundation)](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year?hsLang=en)
- [AG-UI overview](https://promptmetheus.com/resources/llm-knowledge-base/agent-user-interaction-protocol-ag-ui)
- [OpenTelemetry GenAI agent span conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans) (status: Development, so pin the semconv version)
- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications/) and [category walkthrough](https://cycode.com/blog/owasp-top-10-agentic-applications/)
- [LangGraph 1.0 GA announcement](https://changelog.langchain.com/announcements/ann_lxOZRCoFw2Qn9)
- [Microsoft Agent Framework 1.0](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/)
- [Temporal durable AI integrations](https://docs.temporal.io/ai) and [Pydantic AI on Temporal](https://pydantic.dev/docs/ai/integrations/durable_execution/temporal/)
- Project 0 · RAG Platform Foundation Build Guide v0.38 (13 October 2026), the base this platform reuses

