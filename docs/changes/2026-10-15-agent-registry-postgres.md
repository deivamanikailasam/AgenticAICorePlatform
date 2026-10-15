# Agent registry: Postgres default, Backstage catalogue alternative, `just agents`

**Step:** 25 · **Commit:** feat(agent-registry): Postgres registry, Backstage catalogue and just agents · **Date:** 2026-10-15

## What changed
- New package `libs/agent-registry`:
  - `agent_registry.postgres` is the default. It writes to schema `agent_registry` (migration
    `0005`) and is namespaced per environment or test run. A status change takes a row lock and
    writes its history row in the same transaction.
  - `agent_registry.backstage` is the "external catalogue" alternative. Each agent version is a
    Backstage `Component` entity (`spec.type: ai-agent`, lifecycle derived from status) in
    `catalog/agents/<agent>/<version>.yaml`, with history in `history.jsonl`. In a GitOps setup,
    an approval is a reviewed pull request.
  - `sync` registers every spec (including team workers) as draft, with a SHA-256 of the
    canonical spec. Edited content under an old version is refused.
  - `ApprovedOnly` is the gate. In `dev` it allows everything. In `staging` and `prod` the exact
    spec version must be registered with the same content, approved and not expired. Refusals
    are audited (`registry.refused`).
  - `factory.open_registry` picks the registry from `AGENT_REGISTRY`. Staging and prod refuse
    local registries (SQLite or memory).
- `just agents` syncs `agents/` and lists every agent with version, status, owner, risk,
  autonomy, pattern, eval status and tool count. `--approve`, `--deprecate` and `--retire` take
  `--by`, `--reason` and `--expires`.

## Why
This covers Step 25's catalogue, lifecycle states and `just agents`. The spec's default is
Postgres, with an external catalogue as the alternative. The gate is what the next commit
uses so that only approved agents run, or are delegated to, in staging and prod.

## How to check it
`uv run pytest libs/agent-registry`, then `just agents`.

The Postgres contract runs with `-m integration` against the stand-in: 6 passed locally after
`just migrate` applied `0005`.

## Risks and follow-ups
- In the Backstage alternative, the platform record lives in an annotation. A hand edit of
  `spec.lifecycle` alone is overwritten by the next change made through the registry.
