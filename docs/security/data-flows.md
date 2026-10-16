# Data flows and trust boundaries

What crosses which boundary, so each threat in the [threat model](threat-model.md) has a place.
Dashed boxes are trust boundaries: anything crossing one is checked on arrival (identity, policy)
and, if it is content, treated as data, never as instructions.

## 1. A run, end to end

```mermaid
flowchart LR
  subgraph People["Users and apps (untrusted input)"]
    U[User or app]
    AP[Approver]
  end
  subgraph Platform["Project A platform (127.0.0.1 / cluster network)"]
    API[agent-api<br/>run API]
    RT[runtime<br/>patterns, agents, teams]
    TL[tool layer<br/>authorising executor]
    G[guardrails]
    AUD[(audit trail<br/>hash-chained)]
    ST[(run store + checkpoints<br/>encrypted)]
    MEM[(long-term memory<br/>encrypted, policy-gated)]
    SBX[sandbox-runner<br/>gVisor]
  end
  subgraph P0["Project 0 shared services"]
    KC[Keycloak]
    OPA[OPA]
    LLM[LiteLLM gateway<br/>local models]
    BAO[OpenBao]
    RAG[rag-core]
    OBS[OTel / Langfuse]
  end
  subgraph Ext["Business systems and other agents (untrusted output)"]
    MCP[MCP servers<br/>allowlisted]
    A2A[A2A agents<br/>signed cards]
  end
  U -- token --> API
  API -- verify token --> KC
  API --> RT
  RT -- prompts --> G --> LLM
  RT -- tool calls --> TL
  TL -- decide every call --> OPA
  TL -- per-call credential --> BAO
  TL -- on-behalf-of token --> RAG
  TL --> MCP
  TL --> A2A
  TL --> SBX
  MCP -. results are data .-> G
  RAG -. documents are data .-> G
  RT -- writes via policy --> MEM
  RT --> ST
  API -- irreversible action waits --> AP
  AP -- approval bound to the call --> API
  API --> AUD
  TL --> AUD
  RT -. spans, content only to Langfuse, redacted .-> OBS
```

## 2. Data by kind

| Data | Created by | Stored where | Protection | Leaves the platform to | Lifetime |
|---|---|---|---|---|---|
| Run input and output, event log (transcript) | agent-api | run store (Postgres) | encrypted per run and event; tenant and user columns only in clear | the owner's export; Langfuse (redacted content) | 90 days after the run ends ([data lifecycle](../../governance/data-lifecycle.yaml)) |
| Checkpoints | runtime | Postgres | encrypted, bound to tenant, thread and checkpoint | nowhere | 30 days, newest per thread kept |
| Long-term memory | runtime via memory policy | pgvector / Qdrant / Mem0 | encrypted, provenance, PII redacted, untrusted refused | the owner's export | per kind (memory policy) |
| Tool arguments and results | tool layer | audit (digest + redacted copy) | redaction; results are untrusted data | the tool's own system | audit: about 7 years |
| Credentials | OpenBao | never stored by the platform | per call, 5-minute leases, revoked after | the one tool call | minutes |
| Approvals | approval desk | approval store, audit | bound to the exact call; approver from a verified token | approvers' inbox | with the run |
| Traces and metrics | telemetry | Project 0 (Tempo, Prometheus, Langfuse) | content only in Langfuse, redacted; withheld if redaction fails | Project 0 | Project 0's settings |

## 3. Where a person's data can be reached

- **Access:** `GET /v1/me/export` (own data), `POST /v1/admin/export` (privacy officer).
- **Erasure:** `POST /v1/admin/erasure` removes runs, transcripts, checkpoint threads and every
  agent's memories about the person. The audit trail keeps only digests and redacted arguments,
  and records the erasure itself.
- **Correction:** memories are deleted and re-stated by the person; runs are records of what
  happened and are not edited.
- **Retention:** `POST /v1/admin/retention` applies the policy (scheduled with the deployment,
  Step 38).
