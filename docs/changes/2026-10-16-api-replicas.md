# Run API replicas share runs through leases

## What changed
- Each unfinished run is driven by one agent-api replica: the one holding its lease in the run
  store (`claim`, renewed every 10 s, 30 s lifetime). Migration `0009_run_leases.sql` adds the
  lease columns.
- A request for a run held by another replica is handled as follows:
  - state, approvals, resume, retry, cancel and take-over are forwarded to that replica, which
    verifies the token again;
  - events (SSE and WebSocket) are read from the shared store.
  - A forwarded request is never forwarded again.
- When a replica stops renewing (it died or hung), another replica takes its runs over and
  continues them from their checkpoints. This happens on the next request, or within a renewal
  period for unfinished runs nobody holds (`adopt_orphans`). A replica that wakes up and finds
  its lease gone lets the run go without writing anything more.
- Idempotent creation works across replicas. A retried request with the same key returns the
  same run, including when two replicas race (the unique index decides).
- The approvals inbox and thread lists come from the shared store, so every replica gives the
  same answer.
- In Kubernetes the replica's address is the pod IP (`AGENT_API_POD_IP`). Without it, or with
  the memory store, the API behaves as a single process, as before.

## Why
Step 38 asks for HA for the API. Until now a run lived in the memory of the process that
created it, so a second replica could neither see nor approve it, and losing the pod stopped
its runs until a restart.

## How to check it
- `uv run pytest services/agent-api/tests/test_api.py`: 16 passed. Four are new, each with two
  replicas on one store:
  - any replica streams, lists, approves and keeps tenants apart;
  - an idempotent retry on the other replica returns the same run;
  - a stopped replica's paused run is approved through the other and pays once, without
    repeating the step before;
  - a hung replica's running run is taken over without a request, and the old replica writes
    nothing.
- `just test-integration services/agent-api/tests/test_store_integration.py`: the lease SQL in
  Postgres. Covers claim, holder, renew, lapse and take-over, orphans, and the idempotency race.

## Risks and follow-ups
- The review-after queue (Step 28) is still kept by the replica that finished the run.
- Forwarding between replicas is plain HTTP inside the namespace, limited by network policy;
  mTLS between pods (service mesh) is the hardening step in `production.md`.
- On-behalf-of tools in a taken-over run need the user's token again, as after a restart
  (Step 18).
