# ADR 0001: Project A is a separate repo that consumes Project 0 services

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 01
- **Deciders:** repository owner

## Context
Project 0 (RAG Platform Foundation) already runs the gateway, data stores, identity, policy,
observability, audit, eval harness, secrets and deployment. Project A adds the agentic layer.
We must decide whether to grow the RAG monorepo or start a new one.

## Decision
Project A lives in its own uv-workspace repository. It reaches Project 0 only through its
published service interfaces (HTTP, Postgres, Valkey, OIDC, OTLP, S3) and joins Project 0's
Docker network as an `external` network. It never copies, forks or vendors Project 0 code.

## Alternatives considered
| Option | Why not |
|---|---|
| Extend the RAG monorepo | Mixed ownership; agents and RAG would have to release together |
| Fork Project 0 into Project A | Two copies of every shared service drift apart; doubles the security surface |

## Consequences
Clear ownership and independent releases. Project A depends on Project 0's interfaces staying
stable, so integration checks (`just health`, `just smoke`) must run before every release.

## Verification
`just health` reaches every shared service; `scripts/check_compose.py` rejects any service
definition that would duplicate a Project 0 service.
