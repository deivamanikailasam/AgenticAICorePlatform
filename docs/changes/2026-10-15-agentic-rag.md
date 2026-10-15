# An agent that researches company documents, with citations, through rag-core

**Step:** 16 · **Commit:** feat(agent-runtime): add the agentic RAG pattern and its multi-hop golden set · **Date:** 2026-10-15

## What changed
- `pattern: agentic_rag` (also usable on an `agent` node in a hybrid graph). The agent:
  1. decides whether documents are needed at all;
  2. breaks the question into ordered search steps, where a later step can use an earlier
     step's answer ("which battery does the R7 use?" then "who supplies *that* battery?"), and
     picks one of rag-core's architectures (naive, corrective, adaptive, graph, SQL) per step from
     an allowed list;
  3. answers citing passage ids; citations to anything not retrieved are dropped;
  4. checks that every claim is supported by the cited passages and, if not, searches again for
     what is missing (corrective architecture) and rewrites the answer, a bounded number of times.
- Every search is the platform's `search` tool: Project 0's rag-core, called on behalf of the user
  with an exchanged token, under policy and audit. Nothing about retrieval is reimplemented;
  rag-core's citation objects pass through unchanged.
- `evals/golden/agentic-rag/`: 10 documents with access groups and 10 questions (8 multi-hop,
  2 asking about documents the user may not read).
- `agent_compiler.rag_eval` / `just eval-agentic-rag` run each question as Project 0's naive RAG
  (one `naive` search, one answer) and as `agentic_rag`, with the same model, and report answer
  accuracy, citation recall, invalid citations, access violations, model calls, tokens and cost,
  with the dataset size, model alias and time.

## Why
Questions that need two or three documents combined defeat a single search. The spec asks for an
agent that plans retrieval itself while reusing Project 0's retrieval and permissions, and for
proof that it beats naive RAG on multi-hop questions.

## How to check it
`uv run pytest libs/agent-compiler/tests/test_agentic_rag.py` — on both runtimes, against an
offline rag-core double that filters by the user's groups: every golden question runs in both
modes; no answer cites a document the user may not read; restricted facts never leak; a
corrective re-retrieval fixes an ungrounded answer; questions that need no documents trigger no
search; architectures stay within the allowed list; the model-call cap holds.

## Risks and follow-ups
No accuracy numbers are published: the CI runs use a stand-in model. The comparison with naive
RAG needs the corpus ingested into rag-core and the real model (VERIFY-ON-MAC). The naive
baseline is built from rag-core's `naive` search plus one answer call; if rag-core offers its own
answer endpoint, that should become the baseline. Ten questions is a smoke test.
