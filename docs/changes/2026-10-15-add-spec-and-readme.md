# Put the build specification and a front page in the repo

**Step:** 01 · **Commit:** docs(spec): add build guide export, README and progress log · **Date:** 2026-10-15

## What changed
The full build guide, which until now lived only as a Claude Doc, is copied into
`docs/spec/` as Markdown. A README explains what the project is and where to start, and
`PROGRESS.md` records where the build is so that any new session can pick up the work.

## Why
Builders need the plan in the same place as the code, like a construction crew keeping the
architect's drawings on site. A progress log is the site diary: it says what was done today
and what is next.

## How to check it
`ls docs/spec/ && head -5 PROGRESS.md` shows the spec file and "Current step: 1".

## Risks and follow-ups
The export is a snapshot of revision 21. If the doc changes, re-export it and commit the diff.
The two diagrams in the doc are widgets; they are redrawn as Mermaid in `docs/architecture/`.
