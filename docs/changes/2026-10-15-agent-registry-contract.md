# Agent registry contract, lifecycle rules and two local registries

**Step:** 25 · **Commit:** feat(agent-contracts): agent registry contract with lifecycle states · **Date:** 2026-10-15

## What changed
- New switchable part, `agent_registry` (`AgentRegistry`): register, get (newest by version),
  list, set status, set eval status, and history.
- `AgentRecord` holds name, version, owner, description, risk tier, autonomy, pattern, tools,
  team members, eval status, card, spec hash, status (with reason, who, when) and an optional
  approval expiry.
- Lifecycle moves (`AGENT_TRANSITIONS`):
  - draft → approved or retired;
  - approved → deprecated or retired;
  - deprecated → approved or retired;
  - retired is final.

  Every move needs a `by`, and each is kept in the history.
- Re-registering a version with the same content returns the stored record. Changed content
  under the same version is refused, so a reviewed version can't be swapped quietly.
- `runnable()` means approved and not past its expiry (ASI10: owner and expiry).
- Two dummies pass the new `AgentRegistryContract`: `agent_registry.memory` and
  `agent_registry.sqlite`. The SQLite one persists to a file for local use.

## Why
Step 25 needs a catalogue of agents with lifecycle states, so that only approved agents can run
or be delegated to in staging and production. The contract fixes the rules once for every
implementation. The Postgres default comes in the next commit.

## How to check it
`uv run pytest libs/agent-contracts`

## Risks and follow-ups
- Approvals by the owner themselves are not blocked yet. Who may approve is a policy question
  for the approval workflow (Step 28).
