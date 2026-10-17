# Retention runs on a schedule

## What changed
- agent-api applies `governance/data-lifecycle.yaml` every `AGENT_RETENTION_EVERY_HOURS` hours (24
  in the Kubernetes base). It runs as the platform (`retention-schedule`, platform-operator role)
  and is audited as `retention.applied`, like the endpoint.
- Only one replica runs it at a time: a named lock in the run store, a Postgres advisory lock
  (in-process for the memory store). A replica that can't take the lock skips that round.
- Without the variable, retention still runs only on request (`POST /v1/admin/retention`).

## Why
Step 37 deferred scheduled retention to the deployment step. Old runs, checkpoints and expired
memories must go on time without someone calling the endpoint.

## How to check it
- `uv run pytest services/agent-api/tests/test_data_lifecycle.py`: the schedule is refused while
  another replica holds the lock, then prunes and audits as `retention-schedule`.
- `just test-integration services/agent-api/tests/test_store_integration.py`: the advisory lock is
  held by one store at a time and released after.

## Risks and follow-ups
The first round runs one interval after a replica starts. A redeploy postpones it by up to a day,
so data can stay a day past its retention: the cost of avoiding a separate job.
