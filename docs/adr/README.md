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
