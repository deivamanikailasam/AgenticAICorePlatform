# ADR 0009: OPA decides every tool call and data access; OpenFGA for relationship checks

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 01
- **Deciders:** repository owner

## Context
Authorisation logic scattered in tool code is untestable and inconsistent.

## Decision
A `PolicyDecisionPoint` asks Project 0's OPA for every tool call with user, tenant, agent,
tool, side-effect class, data classification, arguments and risk tier. Relationship questions
("is this user a member of the document's team?") go to OpenFGA. Policies are Rego in
`policies/` with unit tests run by `just policy-test` (OPA 1.21.2 locally).

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Checks inside each tool | Inconsistent, untestable, invisible to auditors |
| Cerbos | Good YAML policies; offered as an alternative PDP in the catalogue |

## Consequences
Policy as code, reviewed and tested. OPA latency sits on every tool call, so decisions are
cached per run where safe.

## Verification
Step 10: Rego tests cover allow, deny and approval-required; cross-tenant calls denied and audited.
