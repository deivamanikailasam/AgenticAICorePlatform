# ADR 0008: All model calls go through the Project 0 LiteLLM gateway

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 01
- **Deciders:** repository owner

## Context
Budgets, fallbacks, cost accounting and model audit already exist in Project 0's gateway.

## Decision
Every model call from Project A uses the gateway's OpenAI-compatible API with a per-agent-role
virtual key. Direct provider SDKs are allowed only behind a new ADR.

## Alternatives considered
| Option | Why not |
|---|---|
| Direct provider SDKs | Bypasses budgets, fallbacks and one audit path |
| A second gateway inside Project A | Duplicates Project 0 (violates ADR 0001) |

## Consequences
The gateway is on the critical path; its availability is a Project A SLO dependency.

## Verification
Step 5: every role resolves to a gateway alias; cost per run is recorded from gateway responses.
