# Architecture decision records

An ADR records one hard-to-reverse decision: the context, the decision, the alternatives and
the consequences. Create one with `just adr "<title>"`. An accepted ADR is never edited; a new
ADR supersedes it.

| ADR | Decision | Step |
|---|---|---|
| [0001](0001-separate-repo-consuming-project-0.md) | Separate repo that consumes Project 0 services | 1 |
| [0002](0002-python-uv-fastapi-pydantic.md) | Python 3.12, uv, FastAPI, Pydantic v2 | 1 |
| [0003](0003-declarative-agent-spec.md) | Declarative `agent.yaml` + pattern compiler | 1 |
| [0004](0004-runtime-adapter-langgraph-default.md) | Runtime adapter; LangGraph 1.x default | 1 |
| [0005](0005-two-durability-tiers.md) | Postgres checkpoints + Temporal | 1 |
| [0006](0006-mcp-only-tool-protocol.md) | MCP 2026-07-28 is the tool protocol | 1 |
| [0007](0007-a2a-for-cross-boundary-agents.md) | A2A v1.x across boundaries | 1 |
| [0008](0008-all-model-calls-via-litellm.md) | All model calls via LiteLLM | 1 |
| [0009](0009-opa-decides-every-tool-call.md) | OPA on every tool call; OpenFGA for relationships | 1 |
| [0010](0010-otel-genai-conventions.md) | OTel GenAI semantic conventions | 1 |
| [0011](0011-cloud-build-and-project-0-standin.md) | Cloud build + Project 0 stand-in for integration tests | 1 |
| [0012](0012-component-registry-and-contract-tests.md) | Registry via entry points + shared contract tests | 2 |
| [0013](0013-agent-yaml-v1-layout.md) | agent.yaml v1 layout, secret refs, mandatory budgets | 3 |
| [0014](0014-compiler-plan-shape-and-approval-insertion.md) | Plan shape, compile-time approval gates, stub adapter | 4 |
| [0015](0015-model-access-per-role.md) | Model access per role via the gateway; httpx; AUD conversion | 5 |
| [0016](0016-tool-registry-executor-idempotency.md) | Postgres tool registry, one executor, Valkey idempotency, S3 spill | 6 |
| [0017](0017-mcp-client.md) | MCP client: allowlist, negotiation fallback, trust, OAuth, task-owned connections | 7 |
| [0018](0018-platform-mcp-gateway.md) | Platform MCP gateway: Keycloak, OPA per call, token exchange, audit | 8 |
| [0019](0019-code-sandbox-engines.md) | Code sandbox: gVisor default, Kata option, Firecracker/managed stubs | 9 |
| [0020](0020-tool-authorisation.md) | Tool authorisation: authorising executor, rate limits, on-behalf-of, Cerbos and OpenFGA options | 10 |
| [0021](0021-sandbox-engines-decision.md) | Sandbox engines: Firecracker and Kata implemented and verified on KVM; managed stays a stub | 9 |
| [0022](0022-runtime-adapters.md) | Runtime adapters: LangGraph default, Agent Framework second, shared node semantics, dependency cooldown | 11 |
| [0023](0023-deterministic-workflows.md) | Deterministic workflows: platform saga, journal replay, value paths, stable idempotency keys | 12 |
| [0024](0024-agent-loop-engines.md) | Single tool-calling agent: native ReAct loop, agent_loop slot, framework engines, ADK via A2A | 13 |
| [0025](0025-planning-and-reflection.md) | Planning and reflection: strategies on one agent node, planner/critic slots, one model-call cap, metered comparison | 14 |
| [0026](0026-routing-chaining-and-approval-evidence.md) | Routing, prompt chains with gates, embedder slot, approval evidence bound to the call | 15 |
| [0027](0027-agentic-rag.md) | Agentic RAG: strategy over rag-core, citations only from retrieved passages, grounding check, naive-RAG comparison | 16 |
| [0028](0028-run-api-and-ag-ui.md) | Run API: AG-UI over SSE (WebSocket alternative), in-run events, approvals from verified tokens | 17 |
| [0029](0029-checkpoints-and-durable-runs.md) | Checkpoints at the node runner, encrypted in Postgres; resume by journal replay; durable run API | 18 |
| [0030](0030-temporal-for-durable-long-runs.md) | Temporal for durable long runs: plan interpreted in one workflow, nodes as activities, agent-worker; Restate and DBOS stubs | 19 |
| [0031](0031-long-term-memory.md) | Long-term memory: pgvector default, write policy in front of every store, taint-aware remember, erasure everywhere; Zep and Letta stubs | 20 |
| [0032](0032-context-engineering.md) | Context engineering: per-section budgets, atomic tool groups, rolling summaries capped by budget, trim and offload strategies, JIT tools, untrusted stays data | 21 |
| [0033](0033-supervisor-teams.md) | Supervisor teams: workers as child runs with their own tools and grants, typed shared state, OPA on delegation, cost roll-up, circuit breakers | 22 |
| [0034](0034-team-patterns.md) | Handoff, group chat, map-reduce and debate on one team runtime; every pattern bounded by its own end, turns, items, budget and circuits | 23 |
| [0035](0035-a2a-gateway.md) | A2A gateway: signed Agent Cards, Keycloak + OPA on inbound calls; remote agents as allowlisted tools with pinned card keys | 24 |
| [0036](0036-agent-registry.md) | Agent registry: Postgres default (Backstage alternative), immutable versions, draft → approved → deprecated → retired; only approved agents run or are delegated to in staging and prod | 25 |
| [0037](0037-budgets-loops-and-circuits.md) | Budgets (tree, per tool, wall clock, depth), loop detection by progress, shared circuit breakers, graceful stop or summarise; LiteLLM team budget as the ceiling; ledger reconciled with the gateway | 26 |
| [0038](0038-guardrails.md) | Guardrails: parallel pipeline at input, retrieved, output, tool args and results, MCP descriptions; rules always on, classifier and judge switchable; Presidio PII; quarantine untrusted content | 27 |
| [0039](0039-human-in-the-loop.md) | Human-in-the-loop: approval desk with OPA routing and SLAs, edited arguments bound to evidence, escalation, two-person approval, safe expiry, irreversible tools always gated; review-after and take-over | 28 |
| [0040](0040-agent-identity-and-secrets.md) | Agent identity and secrets: a Keycloak client per agent logging in to OpenBao as itself; two-hop delegated exchange; 5-minute database credentials issued per tool call, revoked after, audited by lease, never seen by the model; secrets only from OpenBao outside dev; cloud managers and SPIFFE as stubs | 29 |
| [0041](0041-agent-telemetry.md) | Agent telemetry: GenAI-convention spans for every agent, model and tool call (pinned commit), MCP spans per MCP conventions; content only to Langfuse, redacted, stripped from every other exporter; metrics over OTLP; SLOs, promtool-tested alerts with runbooks, Grafana dashboard | 30 |
| [0042](0042-agent-audit-trail.md) | One hash-chained audit trail in Postgres; versioned, traced, redacted events; reconstruction from audit plus checkpoints | 31 |
| [0043](0043-governance-artefacts.md) | Governance artefacts generated from specs; CODEOWNERS by risk tier; a CI gate for high-risk changes | 32 |
| [0044](0044-testing-pyramid.md) | The testing pyramid: offline unit tests, cassettes at the edges, MCP contracts, chaos through the real code paths | 33 |
| [0045](0045-agent-evaluation-harness.md) | Agent evaluation: deterministic checks over platform records, generated golden tasks, an honest baseline, a judge calibrated only by people | 34 |
| [0046](0046-simulation-and-red-teaming.md) | Simulation and red-teaming: simulated people and environments, an ASI01-ASI10 suite against the worst model, promptfoo and garak against the real one, findings as issues, a release gate | 35 |
| [0047](0047-ci-eval-gates.md) | CI eval gates per risk tier: regressions, red-team findings and usage growth on every pull request; quality, AUD and latency ceilings on real-model runs; explicit review approval for cost; nightly and release runs | 36 |
| [0048](0048-threat-model-and-compliance.md) | Threat model and compliance as checked data: STRIDE per component with owners, controls and tests; a control matrix with evidence and dated framework mapping; data lifecycle with export and retention | 37 |
| [0049](0049-environments-and-promotion.md) | Environments and promotion: Kustomize + Argo CD, signed images by digest, run leases across replicas, agent versions through a 10% canary with an online-eval gate and automatic rollback, promotion order enforced in CI, measured point-in-time recovery | 38 |
