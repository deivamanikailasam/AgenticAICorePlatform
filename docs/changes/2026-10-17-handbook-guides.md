# Handbook guides: techniques, strategy, laptop to production, lifecycle

## What changed
- New pages in `docs/handbook/`:
  - `techniques.md`: every technique the platform uses, by the problem it solves, with the step
    that explains it.
  - `strategy.md`: the bet, what it buys and costs, where it stands, how to grow it, and the
    principles to keep.
  - `local-to-prod.md`: one path from the Mac through dev, staging, a prod canary and prod,
    with the commands at each stage.
  - `lifecycle.md`: an agent from idea to retirement, with each stage's output, check and gate.
  - `README.md`: the handbook's index.
- The repository README links the handbook and the results table, and its status says that all
  40 steps are built.

## Why
The owner asked for these documents alongside the step guide.

## How to check it
- Commands and paths in the pages exist in the `justfile` and the repository.
- Facts are cross-checked with the ADRs: registry states with ADR 0036, release flow with
  ADR 0049, projects with ADR 0050.
- Numbers come only from `docs/results.md`.

## Risks and follow-ups
These four pages are hand-written, so they can fall behind the code. The compiled pages and the
chapters are the checked sources.
