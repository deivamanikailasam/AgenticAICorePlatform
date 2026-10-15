# Progress

Resume point for any new session. Updated after every commit.

**Current step:** 24 · A2A server and client (next)

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
| 20 Long-term memory | done | `libs/agent-memory`: pgvector store (encrypted, exact search), Qdrant v1.19.1 and Mem0 2.2.1 stores (35 integration tests), Zep and Letta stubs (unsupported/retired upstream); `validating` policy (provenance, untrusted refused, instructions only from platform or confirmed user, injection check, confidence, PII redaction, TTL) and `human_curated`; recall, taint-aware `remember` tool and episodes in the ReAct loop; poisoned-document test (ASI06); erasure, inspect and flush endpoints; `just eval-memory` (10 cases, stand-in model): no memory 0/10, memory 9/10 in-memory and 10/10 pgvector, 0 leaks | real-model eval; Project 0 lifecycle hook registration and `privacy-officer` role in Project 0 Keycloak; memory in the OpenAI Agents and Pydantic AI engines |
| 21 Context engineering | done | `agent_runtime.context`: per-section budgets, atomic tool groups, tool-result compaction with pointers, `rolling_summary` (capped by budget) / `trim` / `offload_s3`, LLM and extractive summarisers, JIT tools with `load_tools`, cache-friendly order, untrusted summaries stay untrusted; `context:` in agent.yaml; native loop windows every call; `just eval-long-dialogue` (5×200 turns, 20 questions, window 8,192, stand-in): rolling summary 20/20 with 0 calls over vs baseline 20/20 with 12 over, trim 15/20, offload 11/20 | real-model run with LLM summaries; embedding ranking for JIT tools; context handling in framework engines |
| 22 Supervisor and hierarchical teams | done | `pattern: supervisor`/`hierarchical` with typed shared state (reads/writes, JSON Schema), LLM and rules dispatchers; workers as child runs with their own tools, grants and budget (worker factory; API assembly); OPA `agent.delegate` (kill switch per agent) audited; worker output delimited and limited to its writes; team budget roll-up, circuit breaker, max_depth, stop without output; per-agent cost spans; `research-report` app (researcher, analyst, writer); 11 team tests + API test + 3 Rego tests | Cerbos delegation policy; per-worker checkpoints; research-report against Project 0 rag-core with a real model (VERIFY-ON-MAC) |
| 23 Handoff, group chat, parallel and debate | done | `pattern: handoff`/`group_chat`/`map_reduce`/`debate` on the Step 22 team runtime (`Crew`): allowed handoff edges, round-robin or LLM speaker selection with `answer_from`, map-reduce with concurrency and max_items, debate with judge; termination by natural end, max_turns, max_items, team budget, circuits, depth; reference specs `support-handoff`, `review-chat`, `batch-summary`, `policy-debate`; `just eval-teams` 15/15 (7 tasks + 8 limit checks, stand-in agents) | real-model runs of the reference specs (VERIFY-ON-MAC) |

## Next

- Step 24: A2A server and client — `a2a-gateway` publishes chosen agents as A2A agents with
  signed Agent Cards; calls remote A2A agents as tools; task lifecycle, streaming and push
  notifications; Keycloak credentials; OPA on outbound and inbound calls; remote messages are
  untrusted input. Done when an agent from another framework calls a Project A agent over A2A
  and vice versa, and an unsigned or unlisted remote agent is refused.

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
| 10 | Zep and Letta memory stores are stubs: Zep Community Edition is unsupported and Letta's self-hosted server is retired; completing either needs a cloud account and a data-residency decision (ADR 0031) | owner |
