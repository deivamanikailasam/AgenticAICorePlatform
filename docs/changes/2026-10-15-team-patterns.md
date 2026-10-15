# Handoff, group chat, map-reduce and debate teams

**Step:** 23 · **Commit:** feat(agent-runtime): handoff, group chat, map-reduce and debate teams · **Date:** 2026-10-15

## What changed
- **Four new patterns**, each a `PatternBuilder` that builds a team node, with settings in a new
  `team:` section of `agent.yaml`:
  - **handoff (swarm):** starts at `entry`. An agent either answers, or hands off (`handoff_to`,
    `note`) to an agent listed for it in `handoffs`.
  - **group chat:** agents take turns on a shared transcript, chosen round-robin or by a selector
    model limited to the team. The chat ends at `stop_phrase`, and the answer comes from
    `answer_from` (otherwise the message before the stop).
  - **map-reduce:** the mapper runs once per item of `input[items]`, at most `concurrency` at a
    time and at most `max_items` items; the reducer combines the results.
  - **debate:** debaters argue in rounds. After each round the judge can end the debate, and it
    must answer after the last round.
- **Shared machinery** (`agent_runtime.teams.Crew`, reusing Step 22): child runs with each
  agent's own tools and grants, `agent.delegate` policy and audit, circuit breakers, depth limit,
  per-agent cost, and one team budget across all agents.
- **Termination.** Every pattern ends on its own condition or at `max_turns`, `max_items`, the
  budget or an open circuit. A team that ends without an answer stops the run; it never reports
  success.
- **Reference specs:** `support-handoff`, `review-chat`, `batch-summary` and `policy-debate`.
- **New eval** `python -m agent_compiler.team_eval` (`just eval-teams`) with golden set
  `evals/golden/teams`: 7 tasks plus 8 limit checks.
- **Found by the eval:**
  - the group chat's answer was the critic's approval instead of the writer's draft, which
    `answer_from` fixes;
  - map-reduce answers are now plain text.

## Why
Step 23 Done-when: "each pattern has a reference spec and eval; no pattern can run past its
budget or termination condition".

## How to check it
`just eval-teams`. This build's run, with stand-in agents, passed 15/15.

| Check | Agent calls | Status |
|---|---|---|
| Handoff ping-pong | 4 | stopped at `max_turns` 4 |
| Critic that never approves | 6 | stopped at `max_turns` 6 |
| 60 documents, over `max_items` 50 | 0 | refused before any call |
| Judge that never decides | 9 | stopped after 3 rounds |

With a budget of AUD 0.002:
- the chat and the debate stop at exactly that budget;
- map-reduce stops at AUD 0.004 (in-flight mappers finish);
- handoff completes inside it.

`uv run pytest libs/agent-compiler/tests/test_teams.py` covers:
- disallowed handoffs;
- policy denial inside a pattern;
- at most 3 mappers running at once when `concurrency` is 3;
- the selector's choices limited to the team, with messages delimited.

## Risks and follow-ups
- Map-reduce checks the budget before starting each item, so mappers already running can
  overshoot it by up to `concurrency` calls.
- Stand-in agents show routing and limits, not how well real models collaborate.
