# ADR 0014: Compiler plan shape, compile-time approval gates and the stub runtime adapter

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 04
- **Deciders:** repository owner

## Context
The compiler turns a spec into something any `RuntimeAdapter` can run. We must decide what the
plan looks like, where safety gates are inserted, and what runs plans before LangGraph lands.

## Decision
1. **Plan shape.** `ExecutablePlan` is a flat graph: typed nodes (`function`, `llm`, `tool`,
   `agent`, `router`, `approval`, `subplan`, `join`), edges with optional outcome conditions,
   one entry, one or more exits, metadata and a SHA-256 `plan_hash` over nodes and edges.
2. **One PatternBuilder per `pattern:` value**, found in the registry. Agent nodes inside a graph
   are expanded with their inner pattern's builder, node ids namespaced `<node>.<inner>`.
3. **Approval gates are inserted by the compiler**, before every tool node whose tool declares
   `approval.required`. Patterns cannot omit them, and the runtime only sees an explicit
   `approval` node. (Step 10 adds the policy-driven gates; Step 28 the approval workflow.)
4. **Stub adapter.** Until Step 11, plans run on `runtime_adapter.sequential` from
   `agent_contracts.dummies`, the same adapter the runtime contract tests use.
5. **Tracing location.** A minimal OTel setup lives in `agent_compiler.tracing` (OTel SDK and
   OTLP/HTTP exporter 1.45.1) until Step 30 moves it into `agent-telemetry`.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Compile straight to LangGraph `StateGraph` | Locks every pattern to one engine (ADR 0004) |
| Nested plans instead of flattening agent nodes | Harder to checkpoint, trace and budget uniformly; may return for sub-workflows (Step 12) |
| Approval checks inside tool code | Easy to forget; invisible in the plan and to reviewers |
| Build `agent-telemetry` now | Out of order; Step 30 owns the full conventions, metrics and audit emitter |

## Consequences
Every adapter must understand the node kinds above. Flattening means node ids can contain
dots. `plan_hash` gives lineage a stable identifier for "what exactly ran".

## Verification
`libs/agent-compiler/tests/test_compiler.py` (expansion, approval insertion, errors, spans) and
`test_trace_in_tempo.py` (integration).
