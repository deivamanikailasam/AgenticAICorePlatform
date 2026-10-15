# The context manager: budgets, summaries, compaction and safe ordering

**Step:** 21 · **Commit:** feat(agent-runtime): context manager with rolling summaries, trim and offload · **Date:** 2026-10-15

## What changed
New module `agent_runtime.context`:
- **Budgets per section.** A transcript is split into system (required), memory, summary,
  history, the current user request (required) and this turn's work. Optional sections share
  the budget by weight; unused share goes to the others.
- **Atomic groups.** A tool call and its results are kept or dropped together.
- **Tool-result compaction.** Long results from earlier rounds keep their beginning and end, with
  a note of what was left out and where the full result is. The latest round is never cut.
- **Three strategies** in the `context_manager` slot:
  - `rolling_summary` (default): the oldest turns are folded into a summary, chunk by chunk.
    The summary is capped at its share of the budget.
  - `trim`: the oldest turns are dropped.
  - `offload_s3`: old turns and long tool results are archived to the artefact store, with
    pointers left in the context.
- **Summarisers:**
  - `LlmSummariser` uses the `summariser` model role, with a prompt that treats delimited text
    as data.
  - `ExtractiveSummariser` needs no model. It keeps what the user stated and never copies tool
    or document text.
- **Cache-friendly order.** System, memory and summary come first, then history in order, then
  the request and work. Between summary updates, consecutive prompts share an identical
  prefix.
- **Untrusted content stays data.** A summary of untrusted content is itself untrusted and
  delimited.
- The registry gains `rolling_summary`, `trim` and `offload_s3`. The Step 2 trim test double is
  now `trim_dummy`; `summary_stub` stays as a test double.

## Why
Step 21: long conversations and tool-heavy runs must stay inside the model's window without
losing what matters, and without letting old tool output turn into instructions (OWASP ASI06,
context side).

## How to check it
`uv run pytest libs/agent-runtime/tests/test_context.py`
- The context manager contract passes for all three strategies.
- A 200-turn conversation fits a 3,000-token budget, and the three stated facts survive in the
  summary.
- Tool calls and their results are never split.
- A summary of a poisoned tool result is untrusted and delimited.

## Risks and follow-ups
- Token counts are estimates (about 4 characters per token), so budgets keep a margin below the
  model's window.
- Wiring into runs and just-in-time tool loading come in the next commit.
