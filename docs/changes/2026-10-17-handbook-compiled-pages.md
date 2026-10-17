# Handbook pages compiled from the chapters and ADRs

## What changed
- `docs/handbook/plain-english.md`: every step's goal, big idea, analogy and recap, by phase.
- `docs/handbook/when-to-choose.md`: every step's alternatives, when each would win, and which
  approach fits which situation.
- `docs/handbook/design-decisions.md`: all 51 ADRs, grouped by the build's nine phases.
- `just handbook` writes them from `docs/guide/` and `docs/adr/README.md`. `just handbook-check`,
  part of `just check`, fails when they fall behind.

## Why
The owner asked for plain-English explanations of every step, a "when to choose what" guide and
the design decisions in one place. The chapters already hold that content. Compiling it means
the summaries can't contradict or drift from the chapters.

## How to check it
`just handbook-check`. Each heading links to its chapter or ADR.

## Risks and follow-ups
A chapter that breaks the 13-section template would drop out of these pages. All 40 chapters
follow it today.
