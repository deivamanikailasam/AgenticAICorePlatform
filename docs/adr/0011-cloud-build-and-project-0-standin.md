# ADR 0011: Build in a cloud container with a Project 0 stand-in stack for integration tests

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 01
- **Deciders:** repository owner

## Context
The spec assumes the build runs on the Mac next to Project 0. On 2026-10-15 the owner chose
to build in a Claude Code cloud container instead. From there Project 0 is unreachable, the
SSH signing key is unavailable, and Project 0's README, ADRs and justfile cannot be read.

## Decision
1. Build and unit-test everything in the container; commits are authored as
   `deivamanikailasam` and are **unsigned** (accepted by the owner).
2. For integration tests only, run a **stand-in stack** (`tests/stack/compose.p0-standin.yaml`)
   made of the same upstream images Project 0 uses (pgvector Postgres, Valkey, Keycloak, OPA,
   LiteLLM, OTel Collector), pinned by digest. It contains no Project A or Project 0 code and is
   never started on the Mac.
3. Every "Done when" item that needs real Project 0 behaviour (its realms, its models, its
   audit log) stays **VERIFY-ON-MAC** in `PROGRESS.md` until run there.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Build on the Mac (recommended in the spec) | Owner's choice was the cloud session; remains the target for final verification |
| Tunnel Project 0 to the container | Exposes local services and secrets to the internet; violates loopback-only ports |
| Mock every service in-process only | Hides real integration bugs (auth, SQL, OPA decisions) |

## Consequences
Integration behaviour is tested against real upstream software, but not against Project 0's
exact configuration. Defaults (ports, realm names, network name) are assumptions recorded in
`.env.example` until checked against Project 0. Commits must be re-verified on the Mac.

## Verification
`just standin-up && just health` passes in CI and in the container; `PROGRESS.md` lists every
VERIFY-ON-MAC item.
