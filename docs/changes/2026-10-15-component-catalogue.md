# Publish a catalogue of every switchable part, generated so it cannot drift

**Step:** 02 · **Commit:** feat(contracts): generate the component catalogue and fail CI when stale · **Date:** 2026-10-15

## What changed
`just catalogue` writes `docs/reference/components.md` from the registry: every slot, every
registered implementation, its status and a one-line summary. Each component now declares a
status — `implemented`, `stub` or `dummy` — and the 46 test doubles show as `dummy`.
`just check` (and therefore CI) fails if the file is out of date.

## Why
A shop's price list printed from the till can't disagree with the till. A hand-written list of
components would go stale the first time someone added one.

## How to check it
`just catalogue-check` prints `catalogue: up to date`. Register a new component without
regenerating and it prints `stale`.

## Risks and follow-ups
The catalogue lists only components whose packages are installed in the workspace.
