# ADR 0004: A runtime adapter interface, with LangGraph 1.x as the default adapter

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 01
- **Deciders:** repository owner

## Context
Control flow (graphs, state, interrupts) is a solved problem; the platform's value is the
governed layer around it. Orchestration frameworks change quickly.

## Decision
All execution goes through a `RuntimeAdapter` interface (`build`, `invoke`, `stream`,
`resume`, `cancel`, `get_state`). The default adapter wraps LangGraph 1.x (1.2.15 on PyPI on
2026-10-15; pinned in Step 11). A second adapter proves the seam.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Write our own graph engine | Re-solves persistence, interrupts and streaming; high maintenance |
| Microsoft Agent Framework 1.0, OpenAI Agents SDK, Pydantic AI, Google ADK | Viable; offered as alternative adapters in the catalogue |
| Bind directly to LangGraph without an adapter | Lock-in; every pattern would depend on LangGraph types |

## Consequences
Patterns depend only on platform contracts. Each adapter must pass the same runtime contract
tests. Some engine-specific features are only exposed where all adapters can support them.

## Verification
Step 11: the same spec passes the same tests on two adapters.
