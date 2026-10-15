# Stand-in: start periods for slow-starting services; longer integration job

**Step:** 30 · **Commit:** fix(ci): give slow stand-in services a start period · **Date:** 2026-10-15

## What changed
- **Start periods.** Health checks now have a start period, during which failed checks don't count:
  - Presidio analyzer: 600 s
  - Langfuse web: 600 s
  - Keycloak: 300 s
- **CI time limit.** The integration job's limit goes from 25 to 45 minutes.

## Why
CI runs 49 and 50 failed in `just standin-up`, before any test ran: `container p0-standin-presidio-analyzer-1 is unhealthy`. The analyzer's gunicorn master started, but its worker, which loads a large spaCy model, was not ready within the 5 minutes the health check allowed (60 × 5 s, no start period). More services now start at once on the 4-CPU runner (Prometheus, Grafana, Alertmanager, then Langfuse and ClickHouse), and the same image passed in runs 43–47. Without a start period, a slow start counts as failure.

## How to check it
CI's integration job gets past `just standin-up`. Locally: `just standin-up` brings every service to healthy.

## Risks and follow-ups
A service that never becomes healthy now takes up to 10 minutes to report. If start-up stays slow, the next options are a larger runner or splitting the Langfuse stack into a compose profile.
