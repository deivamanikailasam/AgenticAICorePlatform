# Add Tempo to the test stand-in so traces can be checked end to end

**Step:** 04 · **Commit:** test(stack): add Tempo 3.1.0 to the Project 0 stand-in · **Date:** 2026-10-15

## What changed
The CI stand-in now runs Grafana Tempo 3.1.0 (pinned by digest), and its OTel Collector
forwards traces to it, the same path Project 0 uses. `P0_TEMPO_URL` (default
`http://127.0.0.1:3200`) tells tests where to look a trace up.

## Why
Step 4 is done only when a run's trace appears in Tempo. Without Tempo in the stand-in, that
could only be checked on the Mac.

## How to check it
`just standin-up && curl -s 127.0.0.1:3200/ready` prints `ready`.

## Risks and follow-ups
Project 0 may run a different Tempo version; the trace format (OTLP) is the same either way.
