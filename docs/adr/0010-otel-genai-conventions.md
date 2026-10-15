# ADR 0010: OpenTelemetry GenAI semantic conventions for all agent telemetry

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 01
- **Deciders:** repository owner

## Context
Traces must work in Tempo and Langfuse today and in other backends later.

## Decision
All spans and metrics follow the OpenTelemetry GenAI semantic conventions (`invoke_agent`,
`chat`, `execute_tool`). These conventions are still at *Development* status, so the semconv
package version is pinned (opentelemetry-semantic-conventions 0.66b1 on PyPI on 2026-10-15;
pinned in Step 30). Export goes over OTLP to Project 0's Collector.

## Alternatives considered
| Option | Why not |
|---|---|
| Vendor SDK tracing (Langfuse-only, LangSmith) | Lock-in; not visible in Tempo |
| Custom span names | Breaks dashboards and tools that understand GenAI conventions |

## Consequences
Portable telemetry. Convention changes require a deliberate semconv upgrade.

## Verification
Step 30: one multi-agent trace visible end to end in Tempo and Langfuse.
