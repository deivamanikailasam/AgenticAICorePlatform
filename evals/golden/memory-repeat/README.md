# Repeat-task memory set

| File | Contents |
|---|---|
| `cases.jsonl` | 10 cases for 4 users. Each user states a preference or fact in one session (`teach`) and a later session asks a question that needs it (`ask`, answer must contain `expect`) |

Users have similar facts on purpose: three users have a delivery preference, so an answer that
uses another user's memory is wrong and is counted as a cross-user leak.

`remember` is the fact the deterministic stand-in model stores in the teach session; real models
ignore it and decide for themselves what to remember. The answering stand-in replies with the
top memory it was given, so offline results measure recall and scoping (the platform's part),
not how well a model uses memory.

Written for this build (synthetic). Small on purpose: one case is 10 percentage points, so treat
results as a smoke test, not a benchmark.

## Running it

```bash
just eval-memory                       # offline: in-memory store, hashing embedder, stand-in model
just eval-memory --store pgvector      # against the agent database (AGENT_DB_DSN)
just eval-memory --model gateway --embedder gateway --store pgvector   # Mac, VERIFY-ON-MAC
```

Each run prints, for "no memory" and "memory" modes: answers correct, cross-user leaks, the
dataset size, store, embedder, model and the time of the run.
