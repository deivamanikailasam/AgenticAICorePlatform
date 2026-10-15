# Step 16 · Agentic RAG

## 1. At a glance

| | |
|---|---|
| **Goal** | An assistant that researches company documents the way a careful person would: decide what to look up, follow a trail across documents, cite what it used, and check itself |
| **Time** | About two days |
| **You end up with** | `pattern: agentic_rag`; a multi-hop golden set with access groups; `just eval-agentic-rag` comparing it with Project 0's naive RAG |
| **New words** | RAG, naive RAG, multi-hop, hop, citation, grounding, corrective re-retrieval |

## 2. The big idea

Retrieval-augmented generation (RAG) means: find relevant passages, then let the model answer
from them. The simple version searches once and answers. That fails when the answer is spread
across documents: "who is the safety officer at the plant that builds the R7?" needs one document
to learn the plant and another to learn the officer. An agentic RAG assistant plans the searches,
follows the trail, cites its sources and checks that the answer really is in them.

> *Analogy:* a librarian versus a search box. The search box returns whatever matches your words.
> The librarian works out that you first need the product sheet, then the plant directory, hands
> you both with the pages marked, and re-checks before you leave.

## 3. What we're doing

1. Build the agentic RAG strategy: plan, hop, answer with citations, self-check, correct.
2. Keep all retrieval in Project 0's rag-core, called as the user.
3. Write a small multi-hop golden set with documents some users may not read.
4. Build a harness that runs naive RAG and agentic RAG on the same set and model.
5. Prove with real Keycloak tokens that each user only sees and cites their own documents.

## 4. Why we're doing this

Knowledge questions are the most common thing people ask an assistant, and the most common place
assistants make things up. Citations make answers checkable; grounding checks catch answers the
citations do not support; and doing all retrieval through rag-core means the assistant can never
reveal a document the person asking is not allowed to read.

> *Watch out:* more hops means more model calls. Agentic RAG is worth it for questions that span
> documents; for one-document questions, naive RAG may be just as good and cheaper. Measure.

## 5. Key ideas

- **Decide whether to retrieve.** Small talk and general questions skip the search entirely.
- **Hops.** The plan lists searches in order (`Q1`, `Q2`…). A later search can use an earlier
  answer: `Q2 = "who supplies the #Q1 battery pack"`.
- **Architectures per hop.** rag-core offers several retrieval methods (naive, corrective,
  adaptive, graph, SQL). The agent picks one per hop from the list the spec allows.
- **Citations only from what was retrieved.** If the model cites a document id that was not in
  the results, the platform drops it. rag-core's citation details pass through unchanged.
- **Grounding check.** A second look asks: is every claim supported by the cited passages?
- **Corrective re-retrieval.** If not, the agent searches for what is missing and rewrites the
  answer, a limited number of times. A still-unsupported answer is marked `grounded: false`.
- **Access control is rag-core's.** Every search carries the user's own (exchanged) token, so
  rag-core returns only documents that user may read.

## 6. How to do it

A knowledge assistant:

```yaml
autonomy: agent
pattern: agentic_rag
models: { default: local-qwen-fast }
budgets: { max_steps: 12, max_wall_seconds: 120, max_tokens: 40000, max_cost_aud: 0.50 }
tools:
  - ref: mcp://rag-core/search
```

As a node in a hybrid graph, with limits:

```yaml
- id: research
  type: agent
  pattern: agentic_rag
  tools: [search]
  max_iterations: 10
  config:
    architectures: [naive, corrective, graph]
    max_hops: 3
    max_corrections: 1
    k: 5
```

Compare with naive RAG on the golden set (Mac, after ingesting the corpus into rag-core):

```bash
just eval-agentic-rag
```

## 7. Benefits

- Questions that span two or three documents get answered instead of guessed.
- Every answer carries citations a person can check.
- Unsupported answers are caught and either fixed or flagged.
- No new retrieval stack: Project 0's rag-core, permissions and guardrails do the retrieving.

## 8. Drawbacks and risks

- More model calls per question than naive RAG (plan, hops, answer, check).
- The plan can be wrong; the grounding check catches unsupported answers, not every bad plan.
- The golden set is small (10 questions): one question is 10 percentage points.
- No comparison numbers yet: they need the corpus in rag-core and the real model (Mac).

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Agentic RAG | Naive RAG (one search) | Single-document questions; lowest cost and latency |
| Agentic RAG | ReAct with the search tool (Step 13) | Mixed tasks where search is one tool among many |
| Self-check by the same role | A separate `judge` role | The answering model is weak at judging itself |
| rag-core `naive` per hop | `graph` or `sql` architectures | Relationship-heavy or tabular questions |

## 10. Which approach fits which situation

- **"What does policy X say?"**: naive RAG or agentic RAG with `max_hops: 1`.
- **"Who approves Y for team Z?"** (policy plus org chart): agentic RAG.
- **Questions over structured data (amounts, counts):** allow the `sql` architecture.
- **Sensitive documents:** nothing extra in the spec; access is enforced by rag-core from the
  user's token, and citations can only come from what that user retrieved.

## 11. Common mistakes

- Running the agent with a service identity: the search tool refuses without a user token.
- Allowing every architecture "just in case": allow what the corpus supports.
- Judging the assistant on questions it cannot answer from the corpus: put those in the golden
  set as "must not answer", as the two access questions do.
- Reporting stand-in numbers: CI counts describe a script, not a model.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| Agent decides whether, what and how to retrieve through rag-core | ✅ | plan call with retrieve flag; no search for questions that need none; hops with `#Q` references; architectures limited to the allowed list; both adapters |
| Decomposition, multi-hop, grounding self-check, corrective re-retrieval, citation pass-through | ✅ | golden questions with 2–3 hops run end to end; an ungrounded answer triggers a `corrective` search and a rewrite; invented citation ids are dropped; rag-core citation objects pass through |
| Every answer keeps citations and access control | ✅ | offline double: no access violations or leaked restricted facts across all 10 golden questions in both modes; stand-in stack: with real Keycloak token exchange, alice retrieves and cites only finance documents and bob only the engineering runbook, even when the model tries to cite everything |
| Multi-hop golden questions beat Project 0's naive RAG on the same set | ⚠️ **VERIFY-ON-MAC** | harness and set ready (10 questions, 10 documents); numbers only from `just eval-agentic-rag` with the corpus ingested into rag-core and the real model |
| ADR | ✅ | ADR 0027 |

## 13. Recap

Step 16 gave the platform a research assistant. It decides whether to search, plans a chain of
searches where later ones build on earlier findings, picks rag-core's retrieval method per step,
answers with citations taken only from what it actually retrieved, checks the answer against
those citations and corrects itself within limits. All retrieval runs through Project 0's
rag-core as the user, so access control is never the agent's to get wrong. A golden set and
harness are ready to show, on the Mac, whether it beats naive RAG on multi-hop questions.
