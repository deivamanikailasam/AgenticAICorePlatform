# ADR 0005: Two durability tiers — Postgres checkpoints for short runs, Temporal for long runs

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 01
- **Deciders:** repository owner

## Context
Some runs finish in seconds; others wait days for a human. Running everything on a workflow
engine adds operational weight; running nothing durably loses work on a crash.

## Decision
`runtime.durability: checkpoint` (default) snapshots state to Project 0 Postgres after every
node, for runs up to roughly 15 minutes. `runtime.durability: temporal` runs the agent as a
Temporal workflow for long or multi-day runs, external signals and strict exactly-once side
effects.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Temporal for everything | Extra service on the hot path for runs that do not need it |
| Restate, DBOS | Lighter durable-execution options; kept as catalogue stubs |
| No durability | Unacceptable: crashes would repeat or lose side effects |

## Consequences
Two code paths to test. Irreversible tools must be idempotent *and* run as Temporal activities
with a stable idempotency key in the Temporal tier.

## Verification
Steps 18–19: kill-and-resume tests with no duplicate side effects.
