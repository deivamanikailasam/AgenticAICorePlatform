# User and environment simulators for multi-turn and fault-injected evals

**Step:** 35 (in progress) · **Commit:** feat(agent-evals): user and environment simulators · **Date:** 2026-10-16

## What changed
- **User simulator (`agent_evals.user_sim`).** A simulated person has a **persona** (style `direct` or `vague`, traits, and facts they can give when asked), a **goal** (utterances from vague to specific, plus an `AnswerCheck` that recognises success), and **patience** (`max_turns`, and `tolerance` for unhelpful replies). `ScriptedUser` is deterministic. `ModelUser` plays the person with a model (role `simulator`, temperature 0, JSON answers). `converse` runs the conversation and records how it ended: goal met, out of patience, the person left, or out of turns. The harness decides success with the goal's check, never with the simulator's say-so.
- **Multi-turn tasks through the run API.** A task with `input._user` is a conversation. Each turn is a run on the conversation's own thread, and the earlier turns are rendered into the new task. The runner adds up the turns' observations (tool calls, delegations, cost). The new check `expected.conversation` (`goal_met`, `max_turns`) is scored as `goal_achieved`.
- **`evals/apps/support-handoff/conversations.jsonl`.** 24 generated conversations: billing or tech goals, vague or direct customers, tolerance 0, 1 or 2. `just eval-tasks` writes the set and checks it.
- **Environment simulator (`agent_evals.env_sim`).** Mock tools are defined in YAML (`ToolSpec` fields plus responses matched on arguments, with `{arg}` placeholders). Seeded faults: error, timeout, malformed output, rate limit after N calls, outages on chosen calls, and latency. Faults are drawn from `Random("<seed>:<tool>")`, so the same seed gives the same faults. The tools are real `Tool` components and pass the Tool contract.
- **Shared runner session.** `eval_runner.session()` is the in-process run API that `run_tasks` now uses. It accepts a policy decision point, which the red-team suite will use for OPA.

## Why
Spec Step 35 asks for user simulators (persona, goal, patience) for multi-turn tasks, and environment simulators with mock tools and seeded faults. Single-shot golden tasks can't show whether an agent recovers from a vague opening, and they can't show how the platform behaves when tools fail in a reproducible way.

## How to check it
- `uv run pytest libs/agent-evals/tests/test_simulators.py`: 15 tests. They cover:
  - the Tool contract;
  - the same seed giving the same faults and another seed differing;
  - scheduled outages and rate limits;
  - vague, direct and impatient customers;
  - facts given when asked;
  - the model-played person;
  - scoring.
- `uv run pytest services/agent-api/tests/test_simulation.py`: 3 tests.
  - Six conversations run against the support team through the run API, using stand-in agents. A vague, patient customer gets a refund on turn 2, after one triage reply. An impatient one leaves after turn 1. Delegations add up across turns.
  - Seeded faults reach the caller as the platform's `failed` and `timeout` errors.

## Risks and follow-ups
- These numbers come from rule-based stand-in agents and check mechanics only. Real conversation success rates need a real-model run (`just eval-app support-handoff --tasks evals/apps/support-handoff/conversations.jsonl --provider gateway`, VERIFY-ON-MAC).
- The runner uses `ScriptedUser`. `ModelUser` is available for open-ended conversations, but the runner doesn't wire it in yet.
