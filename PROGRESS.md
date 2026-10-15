# Progress

Resume point for any new session. Updated after every commit.

**Current step:** 20 · Long-term memory (next)

## Environment note (read first)

This build runs in a **cloud container**, not on the Mac where Project 0 lives (ADR 0011):

- Project 0 is not reachable. Integration checks run against the **stand-in stack**
  (`tests/stack/`, same upstream images). Items needing the real Project 0 are
  **VERIFY-ON-MAC** and are not ticked.
- Commits are authored as `deivamanikailasam`, **unsigned** (accepted by the owner, 2026-10-15).
- Project 0's README/ADRs/justfile were not readable; conventions come from the spec.
- Work happens on branch `claude/wonderful-heisenberg-kssi9q`; it is merged to `main` at the end
  (owner's instruction). Step tags `step-NN` are created on this branch.

## Status by step

| Step | State | Verified here | VERIFY-ON-MAC |
|---|---|---|---|
| 1 Repo, workspace, link to Project 0 | done | `just check`; health, provisioning (idempotent) and `just up` against the stand-in; CI | health/provision/up against real Project 0; service names, realm, network name; SOPS file with Project 0's age key |
| 2 Contracts and component registry | done | 46 dummies pass 23 contracts; `just components`; catalogue check | — |
| 3 The agent.yaml spec | done | readable errors with lines; `just validate agents/`; schema + reference generated and checked | — |
| 4 Pattern compiler and runtime adapter skeleton | done | hello compiles and runs through the stub adapter; trace found in Tempo (stand-in) | trace in Project 0's Tempo/Grafana |
| 5 Model access per role | done | roles resolve (unit + stand-in); tools, structured output with repair, streaming, retries; AUD cost per run/role; capability check in client and compiler | structured output on both real local Qwen models (`-m mac`); capability table vs model cards |
| 6 Tool contract and registry | done | invalid args rejected before execution; duplicate key returns first result (memory + Valkey, race-safe); tool cards generated; Postgres registry contract; S3 spill (stand-in) | registry/idempotency/S3 against Project 0 services |
| 7 MCP client | done | filesystem (stdio, 2025-11-25) + OAuth HTTP server (2026-07-28) discovered, called, traced, audited; unlisted refused (client + compiler) | against Project 0 Keycloak; audit into Project 0's log (Step 31) |
| 8 Platform MCP server | done | gateway with Keycloak auth + OPA per call + audit; alice/bob/gina see only permitted docs via token exchange (stand-in + rag-core double); hardened container via `just up` | real rag-core API shape; Project 0 realm group claims, token exchange, downstream clients; OPA bundle loading |
| 9 Sandboxed code execution | done | gVisor 20261005.0: fork bomb killed, network blocked, 10-min loop cut, OOM stopped, artefacts in/out; Firecracker v1.17.0 and Kata 4.2.0 verified on the KVM CI runner (21 passed) | gVisor inside OrbStack; Firecracker/Kata in a Lima VM with nested virtualisation |
| 10 Tool authorisation | done | Rego 21/21 (allow, deny, approval); cross-tenant call through the live gateway denied and audited; authorising executor with Valkey rate limits; on-behalf-of MCP (server sees alice); Cerbos 0.56.0 parity with OPA on 19 cases; OpenFGA v1.22.0 relationships | Project 0 OPA loading `data.agent.config`; realm group paths; token exchange per audience |
| 11 Runtime adapter | done | LangGraph 1.2.12 and Microsoft Agent Framework 1.19.0 each pass the same 14 runtime contract tests; one agent.yaml gives identical state on both; hello-workflow on LangGraph with its Tempo trace | — |
| 12 Deterministic workflows | done | on both engines: LLM + tool nodes in parallel then routing; saga undoes completed steps newest first (also after a policy denial); model-routed run replays identically without calling the model; sub-workflow; node timeout; tool nodes via the authorising executor with key `<run>:<node>` | — |
| 13 Single tool-calling agent | done | ReAct loop: parallel reads, ordered writes, untrusted observations, bounded repairs, schema repair, `stopped` at max_steps, approval pause/resume, text ReAct fallback; agent_loop slot with native, OpenAI Agents SDK 0.22.3 and Pydantic AI 2.52.0 engines (contract); 8/8 golden tasks (stand-in model) on 3 engines × 2 adapters | golden tasks with the real local model (`-m mac`); Google ADK via A2A in Step 21 |
| 14 Planning and reflection | done | plan_execute (re-plan on failure), rewoo (2 model calls), reflection (self-critique), evaluator_optimiser (judge, best candidate) as pattern builders; planner.llm / critic.llm pass their contracts; one model-call cap per node; all 5 patterns solve the 3 reference tasks on both adapters with a stand-in model, metered | `just compare-patterns` with the real local model (numbers: dataset 3 tasks, model alias) |
| 15 Routing, chaining and hybrid | done | LLM, embedding and rules routers (contract; from agent.yaml on both adapters; confidence fallback); embedder slot with gateway embedder; validation gates and `prompt_chain` / `router` patterns; routing harness over 48 labelled cases (mechanics); approval evidence bound to the call, accepted by OPA (28 tests), Cerbos (12) and rules; invoice-triage reference app end to end on both adapters with the demo ERP over MCP (12 tests) | `just eval-routing` accuracy (dataset 48 cases, model aliases); `just demo-invoice-triage` with the real model; `local-embed` alias |
| 16 Agentic RAG | done | `pattern: agentic_rag`: retrieve decision, hops with `#Q` references, allowed architectures, citations only from retrieved passages, grounding check with corrective re-retrieval; golden set (10 questions, 10 documents) runs in naive and agentic modes on both adapters with no access violations (offline double, stand-in model); real Keycloak token exchange: each user cites only their documents | `just eval-agentic-rag` (agentic vs naive; corpus ingested into rag-core; real model); rag-core answer endpoint as baseline if it exists |
| 17 Run API and streaming | done | `agent-api` (ag-ui-protocol 1.0.0): create (idempotent), AG-UI over SSE and WebSocket, state, cancel, resume (refuses approvals), threads, approvals from verified tokens (self 403, outsider 404); events from inside the run on both adapters; container healthy with public/internal issuer split (also fixed in the gateway); integration: invoice-triage through the real service with Keycloak and OPA; headless Chromium drives the page to approval and payment | real Project 0 Keycloak hostname (`KC_HOSTNAME`) and `P0_KEYCLOAK_PUBLIC_URL` |
| 18 Checkpoints and threads | done | Postgres checkpointer (contract; AES-256-GCM field encryption bound to tenant/thread/checkpoint; retention keeps newest per thread); Valkey (contract) and SQLite alternatives; history and fork; SIGKILL mid-run then resume without repeating the write on both adapters (SQLite) and with Postgres; durable run API: paused runs survive a restart, running runs resume from checkpoints, forks continue after the chosen checkpoint, runs and events encrypted at rest | checkpoint key in OpenBao (Step 29) |
| 19 Durable long runs (Temporal) | done | temporalio 1.34.0; `durability: temporal` runs the plan as the `agent-run` workflow, nodes as `run_node` activities (retries, heartbeats), approvals as signals with a 7-day durable timer, values over 256 KiB by S3 reference; dev server 1.9.1 by digest on 127.0.0.1; `agent-worker` service; durable executor contract; approval wait survives worker stop and server restart with each side effect scheduled once; 24 h wait and expiry on the time-skipping server (CI only: download blocked here); Restate and DBOS stubs | API dispatch of temporal agents with per-node events; delegated credentials in the worker (Step 29) |

## Next

- Step 20: long-term memory — semantic, episodic and procedural memory scoped per user, tenant
  or agent; a `MemoryPolicy` (PII redaction, provenance, confidence, TTL, no unvalidated writes
  from untrusted tool output); right-to-erasure via Project 0's lifecycle; pgvector default with
  Qdrant, Mem0, Letta, Zep alternatives.

## Open issues

| # | Issue | Owner |
|---|---|---|
| 1 | Project 0 unreachable from the build container: live checks deferred (VERIFY-ON-MAC) | owner |
| 2 | Commits unsigned; re-sign or accept when merging | owner |
| 3 | `secrets/dev.enc.env` must be created on the Mac with Project 0's age key | owner |
| 5 | Commit `0f13265` (Step 2 docs) failed CI on Markdown formatting; fixed in `1094c58`. Commits are now made only after `just check` passes on the exact staged tree | done |
| 6 | CI run 9 (Step 7 docs commit) failed: two test modules each set the global OTel tracer provider. Fixed in `88b6b10` (shared fixture) | done |
| 7 | Sandbox engines decided (owner delegated, 2026-10-15): Firecracker implemented and Kata verified on KVM CI; managed service stays a stub by decision (ADR 0021) | done |
| 8 | Approver identity and groups come from the caller of `resume` until the run API takes them from a verified token | done (Step 17) |
| 9 | The proxy in this build container refuses tag pushes (HTTP 403); `step-NN` tags exist locally only. Create them on the Mac or rely on commit messages | owner |
| 4 | LiteLLM key values from provisioning must be moved into OpenBao (automated in Step 29) | Step 29 |
