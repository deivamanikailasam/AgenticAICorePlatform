# Agent releases: canary, shadow, feature flags, online evaluation, automatic rollback

## What changed
- `agent_api.releases` reads an environment's releases file (`AGENT_API_RELEASES`). Per agent it
  names:
  - the stable version;
  - an optional candidate: `canary` serves `weight`% of new threads, and a thread keeps its
    version; `shadow` also runs `weight`% of runs on the candidate in the background;
  - the online-eval gate;
  - feature flags: `enabled`, `disabled_tools`.
- A shadow run never has a side effect. Tools that write answer "shadowed" without being called,
  it asks nobody for approval, the caller never sees it, and it is audited as
  `release.shadow_run`.
- Online evaluation scores every candidate run and a share of stable runs by risk tier (all for
  high). Each score records success, cost and run time, in Postgres (`release_outcomes`,
  migration 0010) or memory. The gate compares the candidate with the stable version over its
  window:
  - minimum runs before judging;
  - failure-rate rise in percentage points;
  - cost and run-time growth in percent.
- On a breach the candidate is rolled back at once on every replica (`release_state`, shared,
  cached for 5 s). The rollback is audited as `release.rolled_back` with its numbers and counted
  (`agent_platform.release.rollbacks`). It raises the new alert `AgentReleaseRolledBack`, which
  has a runbook and a promtool test. A rolled-back version never serves again.
- A candidate the registry has not approved never serves; the stable version does. Runs record
  their spec version and finish on it, including after a restart or a takeover.
- `GET /v1/admin/releases` (platform-operator or operator) shows what serves, the candidate,
  flags and the online-eval verdict with its numbers.
- `Assembly.build(shadow=, disabled_tools=)` wraps the tool executor accordingly.

## Why
Step 38: canary and shadow runs for new agent versions, online evaluation on sampled production
runs, feature flags per agent, and an online-eval gate that rolls back automatically.

## How to check it
- `uv run pytest services/agent-api/tests/test_releases.py`: 7 passed:
  - over 10,000 keys, between 9% and 11% of threads go to a 10% canary;
  - a thread keeps its version;
  - a broken canary fails its first 3 runs and the gate (min 3 runs) rolls it back, so the next
    canary threads run on the stable version, with an audit event and a report through the API;
  - a shadow candidate writes nothing;
  - a switched-off agent is refused (403);
  - a switched-off tool is never called.
- `just test-integration services/agent-api/tests/test_store_integration.py`: rollback state and
  outcomes shared through Postgres.
- `just obs-test`: the new alert fires once per rollback with its runbook.

## Risks and follow-ups
- Durable (Temporal) agents are refused a candidate. Their in-flight runs need Temporal worker
  versioning to finish on their version; that is documented.
- The gate's defaults (20 runs, +5 pp, +25% cost, +50% time) and the stable sample shares are
  owner policy, not measurements. Set them per agent in the releases file.
- The gate scores deterministic signals: status, cost, time. A rubric judge online needs the
  calibrated judge from Step 34 (human labels pending).
