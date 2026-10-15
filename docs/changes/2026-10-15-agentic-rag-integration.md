# Agentic RAG checked with real token exchange: each user sees only their documents

**Step:** 16 · **Commit:** test(agent-compiler): run agentic RAG as two users against the stand-in stack · **Date:** 2026-10-15

## What changed
An integration test runs the agentic RAG agent twice, as alice (finance) and as bob
(engineering). Each search uses the user's own Keycloak token, exchanged for one valid only for
rag-core. The scripted model tries to cite every document in the corpus; each answer keeps only
the documents that user actually retrieved: finance documents for alice, the engineering
runbook for bob.

## Why
"Every answer keeps citations and access control" (Step 16) is only believable with real
tokens: the access decision is rag-core's, made from the exchanged token, and the agent cannot
widen it by citing something it was not given.

## How to check it
With the stand-in stack up and provisioned:
`uv run pytest -m integration libs/agent-compiler/tests/test_agentic_rag_integration.py` — 1 passed.

## Risks and follow-ups
The rag-core here is the test double; the same check against Project 0's rag-core is
VERIFY-ON-MAC.
