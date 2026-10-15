# A repeat-task eval for long-term memory

**Step:** 20 · **Commit:** feat(agent-memory): repeat-task memory eval · **Date:** 2026-10-15

## What changed
- New golden set `evals/golden/memory-repeat`: 10 cases for 4 users. In each case a user states
  something in one session and asks about it in a later one. Several users have similar facts,
  so an answer built from someone else's memory counts as a cross-user leak.
- `python -m agent_memory.repeat_eval` (`just eval-memory`) runs every case without memory and
  with it. It prints answers correct and leaks, with the dataset size, store, embedder, model and
  time.
- The default is offline: in-memory store, hashing embedder, and a stand-in model that stores
  the case's fact and answers with the top memory. Options select pgvector, the gateway embedder
  and the real model.

## Why
Step 20 Done-when: "memory improves a repeat-task eval".

## How to check it
`just eval-memory`. Runs in this build, 10 cases each, stand-in model:

| Store / embedder | No memory | Memory | Leaks |
|---|---|---|---|
| in-memory (keyword overlap) | 0/10 | 9/10 | 0 |
| pgvector / hashing | 0/10 | 10/10 | 0 |

## Risks and follow-ups
- These numbers measure the platform's part (storing, scoping, recall) with a stand-in model,
  not how well a real model uses memory.
- Ten cases is a smoke test: one case is 10 percentage points.
- The real-model run is `just eval-memory --model gateway --embedder gateway --store pgvector`
  on the Mac (VERIFY-ON-MAC).
