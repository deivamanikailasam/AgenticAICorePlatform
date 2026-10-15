# Expose Project 0 retrieval to agents as a governed tool

**Step:** 08 · **Commit:** feat(tools): add the rag-core search tool, called on behalf of the user · **Date:** 2026-10-15

## What changed
`RagCoreSearchTool` (`mcp://rag-core/search`) searches company documents through Project 0's
`rag-core`. It refuses to run without a user, exchanges the user's token for one restricted to
`rag-core`, and never forwards the user's raw token or uses a service identity. It is a `read`
tool with `confidential` data classification; its card is generated with the others.

For CI and cloud sessions only, `tests/stack/fakes/rag_core_fake.py` is a test double of the
search *contract*: it checks the audience-restricted token and filters fixed documents by the
user's groups. It is not a retrieval system and is never deployed.

## Why
Agentic RAG (Step 16) and the reference apps need company knowledge with the same access control
Project 0 already enforces. Reusing rag-core — never re-implementing retrieval — keeps one source
of truth for what each person may read (ADR 0001).

## How to check it
`uv run pytest libs/agent-tools/tests/test_ragcore.py`.

## Risks and follow-ups
The rag-core HTTP API shape (`POST /v1/search`, results with citations) is an assumption until
checked against `~/dev/rag-platform` (VERIFY-ON-MAC).
