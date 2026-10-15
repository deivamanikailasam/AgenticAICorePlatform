# Runs can now be watched step by step

**Step:** 17 · **Commit:** feat(agent-runtime): emit node, tool and text events from inside a run · **Date:** 2026-10-15

## What changed
`agent_runtime.events` lets anything inside a run report what is happening to a per-run
listener (a "sink"), without changing the runtime adapters:
- every node reports that it started, how it finished, and its new value;
- every tool call reports its tool and arguments when it starts, and its result or error when it
  ends;
- model replies that are plain text stream to the listener piece by piece as the model produces
  them; replies that also call tools are sent whole; structured (JSON) replies are not streamed as
  text.
With no listener attached, the wrappers do nothing extra.

## Why
The run API (Step 17) streams live progress to a browser: text as it is written, tool calls as
they happen, state as it changes. Collecting events at the node runner, tool executor and model
client works the same on both runtime adapters and keeps working after a run resumes from an
approval.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_events.py` — 4 passed.

## Risks and follow-ups
Events carry tool arguments and outputs; the API only streams them to people allowed to see the
run. Redaction of sensitive values (PII) comes with guardrails in Step 27.
