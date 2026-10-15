# AgentGuardrailBlockSpike

**Severity:** page · **Fires when:** one guardrail blocks 20 or more times at one stage within
10 minutes.

## What it means
Either an attack (for example prompt injection arriving through retrieved documents, tool results
or MCP tool descriptions) or a broken detector (a classifier or Presidio that fails closed and
blocks everything).

## Check
1. Grafana → *Guardrail hits* by guardrail and action; the `stage` label says where (`retrieved`,
   `tool_result`, `input`, `output`, `tool_args`).
2. Audit log `guardrail.result` events: the findings and which runs, tenants and tools (the
   flagged text itself is never stored).
3. Detector health: Presidio (`P0_PRESIDIO_ANALYZER_URL`), the injection classifier
   (`AGENT_INJECTION_CLASSIFIER_URL`). Findings like "unavailable; blocked" mean fail-closed, not
   attack.
4. Same source? Many blocks from one document collection, one MCP server or one tenant point to
   poisoned content.

## Fix
- Attack: keep blocking (that is the design). Remove the poisoned documents from the source,
  disable the MCP server in `platform.yaml` if its descriptions or results are hostile, and
  check that nothing irreversible ran (approvals and audit).
- Broken detector: restore the service. Do not switch a guardrail off to stop the alert; the
  baseline guardrails can't be switched off from a spec.

## Escalate
Security for suspected attacks, with the run ids and tenants from the audit log. Project 0 for
Presidio.
