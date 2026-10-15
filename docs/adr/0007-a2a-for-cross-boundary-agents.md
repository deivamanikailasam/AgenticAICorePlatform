# ADR 0007: A2A v1.x for cross-boundary agent calls; in-process agents talk through the runtime

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 01
- **Deciders:** repository owner

## Context
Agents call other agents both inside one process (a supervisor and its workers) and across
teams or companies.

## Decision
Inside one run, agents communicate through the runtime's typed shared state. Across process or
organisational boundaries, they use the Agent2Agent protocol, v1.x (v1.0 shipped March 2026;
`a2a-sdk` 1.2.2 on PyPI on 2026-10-15; pinned in Step 24), with signed Agent Cards.

## Alternatives considered
| Option | Why not |
|---|---|
| A2A everywhere, even in-process | Network hops, serialisation and auth overhead for no isolation gain |
| Custom REST between agents | No standard discovery, task lifecycle or streaming |

## Consequences
Two communication paths, both treating messages as untrusted data.

## Verification
Step 24: a different-framework agent calls a Project A agent over A2A and vice versa.
