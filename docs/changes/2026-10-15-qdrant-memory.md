# Memory in Qdrant as an alternative store

**Step:** 20 · **Commit:** feat(agent-memory): Qdrant memory store · **Date:** 2026-10-15

## What changed
- **`memory_store.qdrant`** (qdrant-client 1.19.1) stores memory in Project 0's Qdrant.
  - There is one collection per embedding dimension.
  - Payload indexes cover scope, subject and key.
  - Every query filters on the caller's scope inside Qdrant, so other tenants' and users'
    points never leave the server.
  - Content is encrypted at rest, procedural memory is versioned, and expired points are
    filtered out.
- The stand-in stack gains Qdrant v1.19.1, pinned by digest, with an API key, on 127.0.0.1:6333.
  This is the image Project 0 runs. v1.19.2 is newer but less than 14 days old (ADR 0022).

## Why
The spec lists Qdrant as an alternative for long-term memory, and Project 0 already runs it. It
fits teams that keep their vectors in Qdrant, or that need a dedicated vector engine at larger
scale.

## How to check it
`just standin-up && uv run pytest -m integration libs/agent-memory/tests/test_qdrant_integration.py`
runs the memory store contract.

## Risks and follow-ups
- Finding the newest version of procedural memory takes one extra query per key in the results.
- Production connects with an API key over the internal network. Turn on TLS for Qdrant if it
  leaves that network.
