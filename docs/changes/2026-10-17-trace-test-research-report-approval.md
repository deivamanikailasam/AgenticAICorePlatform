# The end-to-end trace test follows research-report 0.2.0

## What changed
`test_a_team_run_is_one_trace_in_tempo_and_langfuse` now expects the research-report run to
stop and wait for approval to publish. It also expects the team and the critic in the trace,
alongside the app and the other workers.

## Why
research-report 0.2.0 publishes only after a person approves, and it runs its team as a
subgraph with a critic. The test still expected the run to succeed outright, so CI's
integration job failed on 1887123.

## How to check it
`pytest -m integration services/agent-api/tests/test_trace_end_to_end.py` against the
stand-in: 1 passed. It still checks that Tempo gets no content and that Langfuse gets it
redacted.

## Risks and follow-ups
None.
