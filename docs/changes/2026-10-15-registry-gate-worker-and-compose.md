# Registry gate in the Temporal worker too; compose settings

**Step:** 25 · **Commit:** fix(agent-worker): enforce the registry gate for teams run by the worker · **Date:** 2026-10-15

## What changed
- New `agent_api.registry.registry_gate` opens the configured registry, optionally syncs specs,
  and returns the `ApprovedOnly` gate. The API and the agent-worker both use it.
- The agent-worker passes the gate to its Assembly. A team whose agent runs with
  `durability: temporal` is now held to the same rule as one run by the API.
- Compose: agent-api and agent-worker get `AGENT_ENV` (default `dev`) and
  `AGENT_REGISTRY_FILE=/tmp/agents.db`. Their root filesystems are read-only, and a dev setup
  without a database needs a writable path.

## Why
- The previous commit wired the gate into the API only. Teams executed in the worker would have
  delegated to draft agents in staging or prod, which is a gap.
- That commit's change entry also said compose was updated, but the edit had not applied. This
  commit makes the compose change.

## How to check it
- `AGENT_REGISTRY=memory AGENT_ENV=prod uv run python -c "...registry_gate('prod', ...)"` exits
  with "use the shared registry". A prod process refuses a local registry.
- `uv run pytest services/agent-api/tests/test_registry_gate.py` still passes.

## Risks and follow-ups
- The worker does not sync specs. The API registers new versions; the worker only reads.
