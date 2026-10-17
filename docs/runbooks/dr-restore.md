# Restoring the agent database (disaster recovery)

**When:** the agent database (runs, checkpoints, memory, registry, approvals, audit trail) is lost
or corrupted: disk failure, a destroyed volume, a bad migration you can't roll forward from.
**Targets** (`governance/dr.yaml`): lose at most 5 minutes of writes, verified within 30 minutes.
**Rehearsed by** `just dr-drill` (weekly in CI, quarterly on the real environment).

## Before you start
- Stop writers so nothing writes to a half-restored database: scale agent-api and agent-worker to
  zero (`kubectl -n agent-platform-<env> scale deploy/agent-api deploy/agent-worker --replicas=0`).
  Runs in flight are not lost; they continue from their checkpoints after the restore.
- Find the newest base backup and confirm the WAL archive after it is complete.

## Restore
1. Create an empty data directory and copy the base backup into it.
2. Add `recovery.signal` and set `restore_command` to fetch archived WAL. Set
   `recovery_target_timeline=latest`; add `recovery_target_time` only to stop before a known bad
   change.
3. Start Postgres. It replays the archive and promotes itself.
4. In Kubernetes, the Postgres operator does steps 1 to 3 when you create a cluster from its
   object-store backup (VERIFY-ON-MAC: Project 0's operator and backup bucket).

## Verify before reopening
- `just audit-verify`: the audit chain checks out end to end.
- Spot-check a recent run (`GET /v1/runs/{id}`), its checkpoints (`GET /v1/threads/{id}/checkpoints`)
  and a user's memories (`GET /v1/memories?agent=...`). They decrypt only with the same checkpoint
  key from OpenBao, which must not have been rotated without re-encryption.

## Reopen
Scale agent-api and agent-worker back up. Runs that were running resume from their checkpoints.
Paused runs wait for their approvals again. Record the incident and the measured recovery point
and time.

## Escalate
To the SRE lead and the audit and compliance lead (governance/threat-model.yaml owners). If the
audit chain does not verify, treat it as a security incident.
