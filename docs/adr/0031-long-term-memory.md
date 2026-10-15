# ADR 0031: Long-term memory behind one write policy; taint-aware writes; erasure everywhere

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 20
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 20 asks for:
- semantic, episodic and procedural memory, scoped per user, tenant or agent;
- a `MemoryPolicy` that controls writes: PII redaction, a provenance tag, confidence, TTL, and
  no writes from untrusted tool output without validation;
- right-to-erasure hooks that reuse Project 0's lifecycle;
- pgvector by default, with Qdrant, Mem0, Letta and Zep as alternatives.

Done when: memory improves a repeat-task eval, a poisoned document cannot write a persistent
instruction, and erasure removes a user's memories everywhere. OWASP ASI06 (memory and context
poisoning) lists write policy, provenance, validation, TTLs and inspect-and-flush tooling.

Checked on 2026-10-15 under the 14-day cooldown (ADR 0022):
- **Versions pinned:**
  - `qdrant-client` 1.19.1 (2026-09-16), with the stand-in Qdrant image v1.19.1 pinned by digest;
    v1.19.2 is too new;
  - `mem0ai` 2.2.1 (2026-09-25).
- **Zep:** its README says "Zep Community Edition is no longer supported". Zep Cloud needs an
  account.
- **Letta:** its README says the V1 API server, which held archival memory, is "retired" and
  points to Letta Code. The `letta/letta:0.33.0` image starts the Letta Code command-line app,
  not a server.

## Decision
1. **One write path.** Runs read and write memory only through `MemoryService`.
   - Provenance (source, trust, run, subject) is stamped by the platform, never taken from the
     model.
   - Every decision is audited as `memory.write`, with a SHA-256 of the content rather than the
     content.
2. **Default policy `validating`.** It refuses:
   - items with no source;
   - untrusted content that has not been validated;
   - procedural memory unless it comes from the platform or a user who confirmed it, and never
     from untrusted content even if validated;
   - content the injection guardrail flags;
   - items below the minimum confidence.

   It then redacts PII and caps the lifetime: 365 days for facts and 90 for episodes, with no
   limit for instructions until they are replaced, and nothing beyond 730 days. The guardrails
   come from the `guardrail` slot, so Presidio and the classifier arrive in Step 27 without
   changing this.

   Alternatives: `human_curated` (proposals queue for a reviewer) and `read_only`.
3. **Taint-aware writes.** Once a run's context holds untrusted content, the `remember` tool's
   writes count as untrusted, and the policy refuses them. Episodes from such runs keep only the
   user's task and the outcome, never the answer text. This closes the path where a poisoned
   document steers the model into storing what the document said.
4. **Recall keeps trust.** Recalled memories arrive as:
   - system text for platform-authored items;
   - user text for the user's own items;
   - delimited data for anything that came from untrusted content.
5. **Scopes are strict.** An item is visible only if each of its own scope fields is empty or
   matches. Tenant-wide items learned from a user record that user as their subject.
6. **Erasure everywhere.** `POST /v1/admin/erasure` is for privacy officers of the same tenant.
   It stops and deletes the user's runs and events, their checkpoint threads, and every agent's
   memories about them, then audits `subject.erased`. This endpoint is the hook Project 0's
   lifecycle calls.
7. **Inspect and flush.**
   - Users list and delete their own memories.
   - Officers flush everything one run wrote, audited as `memory.flushed`.
   - `MemoryStore` gained `items` and `flush_run`, so every store supports both.
8. **Stores.**
   - **pgvector (default):** encrypted content and exact search.
   - **Qdrant:** Project 0's server, with scope filters in Qdrant.
   - **Mem0** (open-source library on pgvector): fact extraction off, platform embeddings,
     telemetry off, imported only when used.
   - **Zep and Letta:** documented stubs.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Let Mem0 extract facts with a model (`infer`) | The model rewrites content after the policy ran, which reopens the poisoning path. It would win only with the policy applied to each extracted fact |
| Detect injection only, without taint | Detectors miss paraphrases. Taint is structural, and the detector is a second line |
| Zep Cloud or Letta Cloud now | Needs an account, and sends memory to a third party. The owner decides (open issue) |
| Encrypt the whole row | Scope, subject and expiry must be filterable for scoping and erasure |
| ANN index (HNSW) now | The column accepts any embedding dimension. Add the index when the embedding model is fixed |

## Consequences
- After a run reads any tool output, the agent cannot remember new facts without validation,
  through human curation or a validator (Step 27). This is intentional friction.
- Mem0 content is not encrypted by the application, because Mem0 embeds what it stores. Use
  database encryption at rest, or pgvector or Qdrant when encrypted content is needed.
- The audit log keeps user ids after erasure. Pseudonymising it is part of the data lifecycle
  (Step 37).
- Only the native ReAct engine recalls and remembers so far.

## Verification
- `libs/agent-memory/tests/test_memory_in_runs.py`: the preference is recalled in a later run;
  the poisoned document's two writes are refused; the episode has no answer text; a later clean
  run recalls nothing about the attacker's account.
- `services/agent-api/tests/test_erasure.py`: erasure (1 run, 1 thread, 3 memories) and 403s;
  inspect, delete and flush.
- `libs/agent-memory/tests/test_*_integration.py`: the store contract against pgvector, Qdrant
  and Mem0 (35 tests).
- `just eval-memory`: 10 cases, stand-in model.
  - no memory: 0/10;
  - memory with the in-memory store: 9/10;
  - memory with pgvector and the hashing embedder: 10/10;
  - no cross-user leaks.
