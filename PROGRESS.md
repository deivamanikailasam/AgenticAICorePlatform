# Progress

Resume point for any new session. Updated after every commit.

**Current step:** 12 · Deterministic workflows (next)

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

## Next

- Step 12: deterministic workflows — function, LLM and tool nodes, conditional edges, parallel
  branches, joins, sub-workflows, timeouts and saga compensation; replay takes the same path.
  Tool nodes call tools only through the authorising executor, with grants built from the spec.

## Open issues

| # | Issue | Owner |
|---|---|---|
| 1 | Project 0 unreachable from the build container: live checks deferred (VERIFY-ON-MAC) | owner |
| 2 | Commits unsigned; re-sign or accept when merging | owner |
| 3 | `secrets/dev.enc.env` must be created on the Mac with Project 0's age key | owner |
| 5 | Commit `0f13265` (Step 2 docs) failed CI on Markdown formatting; fixed in `1094c58`. Commits are now made only after `just check` passes on the exact staged tree | done |
| 6 | CI run 9 (Step 7 docs commit) failed: two test modules each set the global OTel tracer provider. Fixed in `88b6b10` (shared fixture) | done |
| 7 | Sandbox engines decided (owner delegated, 2026-10-15): Firecracker implemented and Kata verified on KVM CI; managed service stays a stub by decision (ADR 0021) | done |
| 4 | LiteLLM key values from provisioning must be moved into OpenBao (automated in Step 29) | Step 29 |
