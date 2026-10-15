# Langfuse in the stand-in; one multi-agent trace end to end in Tempo and Langfuse

**Step:** 30 · **Commit:** test(observability): one multi-agent trace in Tempo and Langfuse · **Date:** 2026-10-15

## What changed
- **Langfuse in the stand-in.** Langfuse 4.48.0 (web and worker) and ClickHouse 25.12.11 are added, each pinned by digest, with web bound to 127.0.0.1:3100. This follows Langfuse's own compose file. Postgres, Valkey and SeaweedFS reuse the stand-in's services: a `langfuse` database and a `langfuse` bucket, the bucket created by a one-shot `weed shell`.
  - A headless set-up creates the project `agent-platform` with stand-in keys.
  - Langfuse web needed a 2 GB limit and a 1.5 GB Node heap; at 1 GB it ran out of memory.
  - SeaweedFS's `-volume.max` goes from 8 to 32, because each bucket needs its own volumes.
- **Environment.** `standin.env` now has `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY` and the secret key as a dev secret reference.
- **New end-to-end test.** `test_trace_end_to_end.py` runs the research-report team (a supervisor and three workers) through the run API. Spans go to the collector without content, and on to Tempo; they also go to Langfuse with content redacted by the platform's redactor. The test reads the same trace id back from both backends.

## Why
Step 30 "Done when": "One trace shows a multi-agent run end to end in Tempo and Langfuse".

## How to check it
`just standin-up && just test-integration services/agent-api/tests/test_trace_end_to_end.py`. It checks:
- **Tempo:** has all four `invoke_agent` spans, the `chat` spans and `execute_tool search`, and no content attributes.
- **Langfuse:** has the four agents as AGENT observations and the model calls as GENERATION observations. Their input carries `<AWS_ACCESS_KEY>` where the user's task held a key; the key itself appears nowhere.

## Risks and follow-ups
- Langfuse v4 runs in "events_only" mode, where the v1 traces and observations read endpoints are gone. Reads use `/api/public/v2/observations`.
- VERIFY-ON-MAC:
  - Project 0's Langfuse host;
  - the project and its key pair (the secret goes into OpenBao at `langfuse/agent-platform#secret_key`);
  - that its members are only the people allowed to read prompt content.
