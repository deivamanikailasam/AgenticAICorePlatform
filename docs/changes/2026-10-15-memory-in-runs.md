# Agents recall and remember, and a poisoned document cannot plant a memory

**Step:** 20 · **Commit:** feat(agent-memory): long-term memory in agent runs · **Date:** 2026-10-15

## What changed
- **Recall.** Before its first model call, the ReAct loop recalls relevant memories. Each
  arrives with the trust of where it came from:
  - platform-authored memories as system text;
  - the user's own memories as user text;
  - anything validated from untrusted content, delimited as data.
- **Remember.** The `local://platform/remember` tool stores a fact. The model chooses the
  content; the platform stamps the provenance. A write counts as the user's only while the run
  has seen no untrusted content (tool results, documents, untrusted memories). After that it is
  untrusted, and the policy refuses it unless validated. Instructions (`kind: procedural`)
  additionally need the user's confirmation.
- **Episodes.** After the final answer the loop records an episode. A run that saw untrusted
  content keeps only the user's task and the step count, never the answer text.
- **`MemoryService`** is the only read and write path. It applies the user, tenant or agent
  scope, the policy and the TTL. Every decision is audited as `memory.write`, with a SHA-256 of
  the content rather than the content itself. Tenant-wide items learned in a user's run record
  that user as their subject.
- **`memory_for(spec.memory.long_term)`** builds a run's memory from the registry. The spec's
  default policy is now `validating`.
- `executors_for(memory=...)` passes memory to the native ReAct engine. Other engines ignore
  it for now.

## Why
These are Step 20's Done-when checks "a poisoned document cannot write a persistent
instruction" (OWASP ASI06) and memory that helps the next run.

## How to check it
`uv run pytest libs/agent-memory/tests/test_memory_in_runs.py`
- A preference stated in one run is recalled in the next.
- A hijacked model that read a poisoned document tries two writes, and both are refused as
  untrusted.
- The episode keeps no answer text, and a later clean run recalls nothing about the
  attacker's account.

## Risks and follow-ups
- The taint rule is conservative: once a run has read any tool output, the agent cannot
  remember anything without validation. Human curation or a validator (Step 27) is the way to
  approve such facts.
- The OpenAI Agents SDK and Pydantic AI engines do not recall or remember yet.
