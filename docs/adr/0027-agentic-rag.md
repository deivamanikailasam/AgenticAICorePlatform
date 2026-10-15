# ADR 0027: Agentic RAG as a strategy over rag-core, with grounding checks and a naive-RAG comparison

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 16
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 16 asks for an agent that decides whether, what and how to retrieve using `rag-core`
through MCP: query decomposition, multi-hop retrieval, a grounding self-check, corrective
re-retrieval and citation pass-through, with rag-core architectures selectable per call (naive,
corrective, adaptive, graph, SQL), reusing Project 0's retrieval and guardrails. Done when:
multi-hop golden questions beat Project 0's naive RAG on the same set, and every answer keeps
citations and access control.

Checked 2026-10-15: no new dependency is needed. Retrieval frameworks (LlamaIndex, LangChain
retrievers, Haystack) would duplicate what rag-core already does under Project 0's permissions,
which the spec rules out.

## Decision
1. **`pattern: agentic_rag` is a strategy on one AGENT node** (the Step 14 shape: capped model,
   per-call copy, approvals refused), whole-spec or as an `agent` node in a hybrid graph.
2. **Retrieval is only the platform `search` tool** (`mcp://rag-core/search`): rag-core called
   on behalf of the user with an exchanged token, under policy and audit. In-process agents use
   this shared tool directly; remote clients reach the same tool through the MCP gateway
   (Step 8). Routing an in-process agent back through its own gateway would add a second token
   exchange and network hop without adding any control.
3. **Plan, then hops:** one structured call decides whether to retrieve and lists ordered hops
   (`Q1…`, at most `max_hops`, default 3); a hop may use an earlier hop's answer (`#Q1`), and only
   hops whose answers are needed get an intermediate answer call. The architecture per hop must
   come from the node's allowed list; anything else falls back to the first allowed.
4. **Citations come only from retrieved passages:** ids the model invents or that were not
   retrieved are dropped; rag-core's citation objects pass through unchanged. An answer can
   therefore never cite a document the user could not retrieve.
5. **Grounding self-check and corrective re-retrieval:** a structured check of every claim
   against the cited passages; on failure, search for what is missing (the `corrective`
   architecture when allowed) and rewrite, at most `max_corrections` times (default 1). An answer
   that is still ungrounded is returned with `grounded: false` so a gate or router can act on it.
6. **Baseline and harness:** "Project 0's naive RAG" is one `naive` search plus one answer call
   with the same model and tool. `agent_compiler.rag_eval` scores both modes on the golden set
   (answer correct, citation recall, invalid citations, access violations, cost) and states
   dataset size, model alias and time. Numbers only from real runs.
7. **Golden set** `evals/golden/agentic-rag`: 10 documents with access groups, 8 multi-hop
   questions and 2 access questions; `plan`/`hop_answers` fields script the CI stand-in only.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| A retrieval framework inside the platform | Duplicates rag-core and bypasses Project 0's permissions and guardrails |
| One retrieval per question, larger `k` | Cannot follow a chain (R7 → BP-200 → VoltCell); this is the naive baseline |
| ReAct with a search tool (Step 13) | Works, but has no structured hops, citation filtering or grounding check; kept as an option via `pattern: react` |
| Always self-check with a separate judge role | Extra cost for every answer; the check uses the node's role, and a judge can be swapped in later |
| Calling the gateway over MCP from in-process agents | Second exchange and hop, no added control (decision 2) |

## Consequences
Each multi-hop answer costs several model calls (plan, needed hop answers, final answer,
grounding, plus any correction); the comparison harness measures it. If rag-core exposes its
own answer endpoint, the baseline should use it (VERIFY-ON-MAC). The golden corpus must be
ingested into rag-core with its access groups before the Mac run.

## Verification
`libs/agent-compiler/tests/test_agentic_rag.py` (both adapters; access, leaks, corrective
re-retrieval, no-retrieval path, allowed architectures, cap) and
`test_agentic_rag_integration.py` (real Keycloak token exchange, two users).
