# Text-to-vector access through the platform gateway

**Step:** 15 · **Commit:** feat(contracts): add the embedder slot and the gateway embedder · **Date:** 2026-10-15

## What changed
- A new component slot, `embedder` (the 26th): turn texts into vectors. Its contract says the
  same text always gives the same vector, every vector has the same length, and similar texts
  are closer than unrelated ones.
- Two offline dummies for tests (`embedder.hashing`, `embedder.word_hashing`): they hash words
  and letter groups into a fixed-size vector. They know no synonyms, so they test plumbing only.
- `embedder.gateway` calls Project 0's LiteLLM gateway (`/v1/embeddings`) under the `embedder`
  role, with its own key, in batches, returning vectors in input order and recording cost.
- `platform.yaml` maps the `embedder` role to `local-embed`.

## Why
The embedding router (next commit) compares a request with labelled examples by meaning. The
model behind it must come from Project 0's gateway like every other model, not from a library
loaded inside the platform.

## How to check it
`uv run pytest libs/agent-contracts libs/agent-runtime/tests/test_embeddings.py`.

## Risks and follow-ups
VERIFY-ON-MAC: the alias `local-embed` is an assumption; set it to the embedding model Project 0's
LiteLLM actually serves.
