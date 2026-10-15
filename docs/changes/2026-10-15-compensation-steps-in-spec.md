# Specs can name an "undo" step for each write

**Step:** 12 · **Commit:** feat(spec): treat compensation steps as saga-only nodes · **Date:** 2026-10-15

## What changed
A node's `compensation:` names another node that undoes it if a later step fails (the saga
pattern). Those undo nodes run only when the saga unwinds, so the validator and compiler now
treat them specially: they are never the start of a graph, never reported as "unreachable",
never counted as an end, and they may not have edges of their own (a clear error says so).

## Why
Like the "return to sender" label on a parcel: it is not part of the normal delivery route, it
only matters when something goes wrong, and it must not be confused with a real stop.

## How to check it
`uv run pytest libs/agent-spec/tests/test_loader.py -k compensation` and the compiler test
`test_compensation_steps_are_not_starts_or_unreachable` (next commits).

## Risks and follow-ups
None beyond the saga runtime itself (next commit).
