# ADR 0003: A declarative `agent.yaml`, validated by JSON Schema and compiled by a pattern compiler

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 01
- **Deciders:** repository owner

## Context
Application teams need one reviewable artefact that says what an agent is allowed to do:
pattern, models, tools, budgets, approvals, memory, guardrails and evals.

## Decision
Every application is described by an `agent.yaml` (`spec_version`-ed). Its JSON Schema is
generated from Pydantic models (Step 3). A pattern compiler (Step 4) turns it into a runnable
plan. Code is only used for custom functions, tools and patterns registered through the SDK.

## Alternatives considered
| Option | Why not |
|---|---|
| Code-only agent definitions | Hard to review, diff and govern; no single place to see permissions |
| A visual builder as the source of truth | Not diffable in Git; can be added later as an editor of `agent.yaml` |

## Consequences
Governance (cards, policy, CODEOWNERS by risk tier) can read the spec directly. The schema
becomes a public contract that needs versioning and migrations.

## Verification
`just validate agents/` (Step 3) fails on invalid specs with readable errors.
