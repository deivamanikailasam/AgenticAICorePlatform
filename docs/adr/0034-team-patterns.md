# ADR 0034: Four team patterns on one team runtime, each with guaranteed termination

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 23
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 23 asks for:
- handoff (swarm): an agent transfers control and context to a specialist;
- group chat with a speaker-selection policy;
- parallel fan-out with map-reduce, under concurrency limits;
- debate or critic panels with a judge;
- termination conditions on every pattern;
- each pattern as a `PatternBuilder`.

Done when: each pattern has a reference spec and eval, and no pattern can run past its budget or
termination condition. Step 22 already provides child runs with each agent's own tools and
grants, `agent.delegate` policy, circuit breakers and a team budget. No new packages were
needed.

## Decision
1. **One team node, one runtime.**
   - Each pattern builder emits the team node with `mode` set to the pattern.
   - `agent_runtime.teams.Crew` holds what all patterns share: worker runs, policy, circuits,
     per-agent cost, the budget, depth and the history.
   - A pattern is a small function over the crew.
2. **Settings** go in a `team:` section of `agent.yaml`, checked against the spec's agents:
   `entry` and `handoffs`; `speaker`, `stop_phrase` and `answer_from`; `items`, `mapper`,
   `reducer`, `concurrency` and `max_items`; `debaters` and `judge`; `max_turns`.
3. **Termination on two levels for every pattern.**
   - Its natural end: an answer instead of a handoff, the stop phrase, the reduce step, or the
     judge's `done`.
   - Hard limits: `max_turns`, `max_items` (refused up front), the team budget, circuits and
     depth.
   - Ending without an answer stops the run, never succeeds.
4. **Safety carried over.** Handoffs only along the listed edges. The speaker selector is
   constrained to the team and sees messages delimited as data. Every hand-over goes through
   policy.
5. **Eval with adversarial cases.** Golden tasks run on the reference specs, plus agents that
   never end on their own. A limit case passes only when the run stops within the number of
   calls its limit allows.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| A node kind per pattern | Four copies of policy, budget and circuit logic, and more ways to get one wrong |
| Framework group chats (AutoGen-style) | Engine-specific, with their own termination semantics outside our policy and budget |
| Budget checks only after each call | Fine for sequential patterns, but parallel fan-out also needs the check before each start; overshoot is bounded by `concurrency` |
| Chat answer = last message | The last message is often a critic's approval, not the work. Hence `answer_from` |

## Consequences
- Map-reduce can overshoot the budget by the mappers already in flight, at most `concurrency`
  calls.
- Handoff context (notes) reaches the next agent as part of its input. Agents must treat it as
  data, like any agent output.

## Verification
- `just eval-teams`: 7 tasks plus 8 limit checks, 15/15 passed in this build (stand-in agents).
  - Handoff ping-pong stops at 4 calls.
  - The never-approving chat stops at 6.
  - 60 items over the limit of 50 are refused with 0 calls.
  - The undecided judge stops after 9 calls.
  - Budget-capped runs stop at the budget (map-reduce one in-flight wave over it).
- `libs/agent-compiler/tests/test_teams.py`: disallowed handoff; policy denial; at most 3
  mappers at a time with concurrency 3; selector constrained and delimited.
