# Data lifecycle: export a person's data, and retention for runs, checkpoints and memory

**Step:** 37 (in progress) · **Commit:** feat(agent-api): subject export and retention per the data lifecycle policy · **Date:** 2026-10-16

## What changed
- **`governance/data-lifecycle.yaml`.** How long each kind of data is kept:
  - finished runs and their event logs (the transcripts): 90 days;
  - checkpoints: 30 days, keeping each thread's newest;
  - memory: by each kind's lifetime;
  - the audit trail: about 7 years, append-only, holding no run content;
  - traces: Project 0's settings;
  - reports: CI artefacts.

  Also how a person gets access, erasure and correction. The values are owner policy for legal and privacy review, not derived from any law.
- **Export (access, Australian Privacy Principle 12).**
  - `GET /v1/me/export` returns the caller's runs with their event logs, their threads, and every agent's memories about them.
  - `POST /v1/admin/export` does the same for one user, for a privacy officer of that tenant.
  - Every export is audited (`subject.exported`).
- **Retention.** `POST /v1/admin/retention`, for a privacy officer or platform operator, prunes:
  - finished runs older than the policy, with their event logs (`RunStore.prune`, memory and Postgres);
  - old checkpoints, keeping each thread's newest;
  - expired memories.

  A run that is still going, or waiting for a person, is never pruned. The job is audited (`retention.applied`), and the scheduled call comes with the deployment (Step 38).
- **Wiring.** The agent-api container mounts the policy at `/app/governance/data-lifecycle.yaml` (`AGENT_DATA_LIFECYCLE`). Without a policy, retention answers 503. `pyyaml` is now a declared dependency of agent-api.

## Why
Spec Step 37 asks for a data lifecycle for runs, memory and transcripts: retention, erasure and export. Erasure existed (Step 20). Export and retention for runs and transcripts didn't.

## How to check it
`uv run pytest services/agent-api/tests/test_data_lifecycle.py`: 3 tests.
- **Export:** a person exports their own run, its transcript and their memories, with no one else's data. Another user and another tenant's privacy officer are refused (403). The tenant's privacy officer can export. Both exports are audited.
- **Retention:** two runs are 120 days old; the finished one is pruned with its events, and the one waiting for a person stays. A user without the role is refused. The job is audited.
- **Policy:** the policy covers runs, checkpoints, memory and audit, and never lists a running or waiting status as finished.

## Risks and follow-ups
- Retention values need legal and privacy review before production.
- The scheduled retention call is added in Step 38, with the deployment.
