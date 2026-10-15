# Agentic RAG golden set (multi-hop)

| File | Contents |
|---|---|
| `corpus.jsonl` | 10 short company documents, each with the groups allowed to read it |
| `questions.jsonl` | 10 questions: 8 need two or three documents combined (multi-hop); 2 ask for documents the user may not read and must not be answered from them |

Each question names the user and their groups, the expected answer (`expect`, matched
case-insensitively; `null` means the answer must not contain the restricted fact), the documents
a correct answer must cite (`required_docs`) and documents it must never cite
(`forbidden_docs`). `plan` and `hop_answers` script the deterministic stand-in model used in CI;
real models ignore them.

Written for this build (synthetic). Small on purpose: one question is 10 percentage points, so
treat results as a smoke test, not a benchmark.

## Running it against Project 0 (Mac, VERIFY-ON-MAC)

1. Ingest `corpus.jsonl` into rag-core with each document's `groups` as its access list (use
   rag-core's own ingestion; its API is not assumed here).
2. Give each user in `questions.jsonl` (alice, tom) the listed groups in Keycloak.
3. `just eval-agentic-rag` runs every question twice, as Project 0's naive RAG (one search with
   the `naive` architecture, one answer) and as `pattern: agentic_rag`, with the same model, and
   prints answer accuracy, citation recall, invalid citations, access violations, model calls,
   tokens and cost, with the dataset size, model alias and time of the run.
