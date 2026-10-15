# Architecture

Project A turns an `agent.yaml` into a governed, running system on top of Project 0's shared
services. This page is the overview; detailed C4, data-flow and sequence diagrams are added as
the steps that build each part land.

## Layers (redrawn from the spec's architecture widget)

```mermaid
flowchart TB
  subgraph Entry["Entry"]
    API["agent-api<br/>runs · threads · approvals · AG-UI over SSE"]
    MCPG["mcp-gateway<br/>platform tools · proxy to external MCP"]
    A2AG["a2a-gateway<br/>Agent Cards · remote agents"]
  end
  subgraph Core["Spec, compiler and runtime"]
    SPEC["agent-spec<br/>agent.yaml schema + validation"]
    COMP["agent-compiler<br/>spec → ExecutablePlan"]
    RT["agent-runtime<br/>RuntimeAdapter (LangGraph default)"]
    PAT["agent-patterns<br/>workflow · ReAct · plan-execute · supervisor …"]
  end
  subgraph Services["Platform services acting on every step"]
    TOOLS["agent-tools<br/>registry · MCP client · sandbox · OPA"]
    MEM["agent-memory<br/>checkpoints · long-term memory · context"]
    SAFE["agent-safety<br/>budgets · loops · guardrails · approvals"]
    TEL["agent-telemetry<br/>GenAI spans · metrics · audit"]
  end
  subgraph P0["Project 0 shared services (reused, never forked)"]
    GW["LiteLLM gateway"]
    PG["Postgres + pgvector"]
    VK["Valkey"]
    S3["SeaweedFS S3"]
    KC["Keycloak"]
    OPA["OPA / OpenFGA"]
    BAO["OpenBao"]
    OTEL["OTel Collector → Tempo · Loki · Prometheus · Langfuse"]
    AUD["Hash-chained audit log"]
    RAG["rag-core"]
  end
  API --> SPEC --> COMP --> RT --> PAT
  PAT --> TOOLS & MEM & SAFE & TEL
  MCPG --> TOOLS
  A2AG --> RT
  TOOLS --> OPA & BAO & RAG
  PAT --> GW
  MEM --> PG & VK & S3
  SAFE --> KC & OPA
  TEL --> OTEL & AUD
```

Requests enter through the API or a gateway; the compiler and runtime execute the spec; the
four platform services act on every step; everything runs on Project 0's shared services.

## Roadmap (redrawn from the spec's roadmap widget)

```mermaid
flowchart LR
  A["A · Foundation<br/>Steps 1–4"] --> B["B · Models, tools, MCP<br/>5–10"] --> C["C · Runtime and patterns<br/>11–17"]
  C --> D["D · State, memory, durability<br/>18–21"] --> E["E · Multi-agent and A2A<br/>22–25"] --> F["F · Safety, HITL, identity<br/>26–29"]
  F --> G["G · Telemetry, audit, governance<br/>30–32"] --> H["H · Evals and red-teaming<br/>33–36"] --> I["I · Production and reuse<br/>37–40"]
```

## How Project A connects to Project 0 (Step 1)

```mermaid
flowchart LR
  subgraph net["Docker network: rag-platform (owned by Project 0)"]
    subgraph pa["Project A (infra/compose/compose.yaml)"]
      H["agent-health (one-shot)"]
    end
    subgraph p0["Project 0"]
      GW2["litellm:4000"]
      PG2["postgres:5432"]
      VK2["valkey:6379"]
      KC2["keycloak:8080"]
      OPA2["opa:8181"]
      OT2["otel-collector:13133"]
    end
  end
  H --> GW2 & PG2 & VK2 & KC2 & OPA2 & OT2
```
