# Guardrails around every model and tool call; MCP tool descriptions screened

**Step:** 27 · **Commit:** feat(agent-api): guardrails around every model and tool call, MCP descriptions screened · **Date:** 2026-10-15

## What changed
- `agent_guardrails.guarded` adds two wrappers.
- `GuardedModelClient`:
  - The user's request is checked at the `input` stage; a block stops the run with
    `guardrail: input blocked (…)`.
  - Untrusted messages (tool results, retrieved documents, memory, other agents) are checked at
    `retrieved`. A flagged message is quarantined: its content is replaced by a notice, so the
    model never sees the injected text.
  - The answer is checked at `output`: redactions are applied, and a block stops the run.
  - Streaming answers are collected, checked, and then sent as one chunk.
- `GuardedToolExecutor`:
  - Arguments are checked at `tool_args`, against the tool's schema and the spec's limits. A
    block refuses the call as `denied`, with the reason.
  - Results are checked at `tool_result`; injected results are quarantined, and secrets and PII
    redacted.
  - Verdicts on untrusted text are cached by content.
- MCP: `register_all(..., screen=)` leaves out a tool whose description or parameter
  descriptions carry an injection. The refusal is audited as `mcp.tool_refused`.
- The run API's assembly builds the pipeline from `spec.guardrails`. It always adds the baseline
  (`prompt_injection`, `secret_leak`) and passes the judge model when the LLM judge is chosen.
  Model-call findings are audited with the run's context.
- The A2A test now proves that a partner agent's hostile reply is quarantined. Before this
  change it was only returned as data.

## Why
This puts Step 27's guardrails "before and after each model and tool call", and covers indirect
injection through tools and tool descriptions.

## How to check it
`uv run pytest services/agent-api/tests/test_guardrails_in_runs.py` (5 tests, through the run
API):
- A poisoned search result is quarantined. The model never sees "AUD 9000", no payment is made,
  and the finding is audited.
- A direct injection in the request stops the run before any model call.
- A payment of 9000 is refused against a limit of 500 before the tool runs.
- An AWS key in the answer is redacted.
- The MCP tool `wipe_disk`, whose description says "IGNORE PREVIOUS INSTRUCTIONS", is never
  registered.

## Risks and follow-ups
- Rules-based detection has false positives and misses. The eval suite (next commit) measures
  both on a labelled set.
