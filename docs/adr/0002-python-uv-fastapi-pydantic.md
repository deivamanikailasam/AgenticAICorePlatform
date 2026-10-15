# ADR 0002: Python 3.12, uv workspace, FastAPI and Pydantic v2

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 01
- **Deciders:** repository owner

## Context
The platform needs the widest AI library coverage (LangGraph, MCP and A2A SDKs, Temporal,
OpenTelemetry) and should share a toolchain with Project 0.

## Decision
Python 3.12 (3.12.13 installed by uv), uv 0.11.x workspace with one `uv.lock`, FastAPI for
HTTP services, Pydantic v2 (2.14.x) for every contract and the `agent.yaml` schema. Ruff
0.17.0 for lint and format, mypy 2.4.0 strict on libraries, pytest 9.1.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| TypeScript (Node 22) | Good MCP and AG-UI support, but weaker eval, Temporal-AI and data tooling; different toolchain from Project 0 |
| Python 3.13 / 3.14 | Newer, but some AI libraries (litellm, presidio) cap `requires-python`; 3.12 keeps parity with Project 0 |
| Poetry / pip-tools | uv is faster and handles workspaces natively |

## Consequences
One language across libraries and services. The pinned Python minor version must be revisited
when Project 0 moves.

## Verification
`requires-python = ">=3.12,<3.13"` in every package; CI runs on 3.12.
