# Reconstruct a run from the audit trail plus its checkpoints

**Step:** 31 · **Commit:** feat(agent-audit): reconstruct a run from audit plus checkpoints · **Date:** 2026-10-15

## What changed
- **`agent_audit.reconstruct`.** `reconstruct(run_id, records, checkpoints)` and `replay(run_id, audit, checkpointer)` build a run's story: who, which agent and versions, the trace ids, the plan, and every audit event and checkpoint in time order, including team workers' child runs. It also cross-checks the two sources:
  - every event still matches its own hash
  - the run is bracketed by `run.started` and `run.ended`
  - every checkpointed node is in the audited plan
  - every approved call ran with exactly the approved arguments (same tool, same digest)
  - the output rebuilt from the checkpoints has the digest `run.ended` recorded
- **`just audit-replay <run_id>`.** Prints the story and the checks. Exit status 0 when every check passes, 1 when one fails, 2 when the run is unknown.
- **Supporting changes:**
  - Approval events now carry `calls`: each call's tool and arguments digest, using the edited arguments when the approver edited them.
  - The `plan` event carries the plan's exits.
  - The input/output digest function moved to `agent_audit.redact.digest`.

## Why
Step 31's "Done when" includes "a run can be reconstructed from audit plus checkpoints". The cross-checks turn the reconstruction into evidence: a forged checkpoint, or a payment that ran with arguments other than the approved ones, shows up as a failed check.

## How to check it
- `uv run pytest services/agent-api/tests/test_audit_trail.py`:
  - An approved payment is reconstructed with all five checks passing. Its output is rebuilt from the checkpoints.
  - A forged checkpoint value fails only "output matches run.ended".
  - A changed call digest fails "approved calls ran with the approved arguments" and "events match their hashes".
- With the stand-in (`-m integration`), the same run is written to the Postgres trail and encrypted Postgres checkpoints, and `just audit-replay`'s code path reconstructs it with no failed check.

## Risks and follow-ups
- The output check rebuilds the output with the same rule as the runtime (the last exit node that has a value). A run whose output a budget summariser replaced would fail this check. That is accurate, because the output did not come from the plan.
- Child runs on other workers' threads are found only if the trail names their thread.
