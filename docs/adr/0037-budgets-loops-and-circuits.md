# ADR 0037: Budgets, loop detection and circuit breakers in every run; gateway budgets as the backstop

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 26
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 26 asks for:
- a `BudgetController` enforcing per-run and per-agent limits: steps, tokens, AUD cost,
  wall-clock time, tool calls per tool, and delegation depth;
- hierarchical budgets mapped to LiteLLM virtual keys;
- a `LoopDetector` for repeated identical tool calls, no-progress cycles and oscillating plans;
- circuit breakers on failing tools and models;
- a graceful stop with a partial result and reason;
- alternatives: hard stop, or stop and summarise.

Done when: a deliberately looping agent stops within its limits, the run record shows which
limit tripped, and cost dashboards match the gateway's accounting.

## Decision
1. **Platform budget controller.**
   - Budgets form a tree: a child run opens under its parent, its spending rolls up to every
     ancestor, and an ancestor's trip stops every child.
   - Wall-clock time counts from opening, not from what callers report.
   - `max_steps` stays with the runtime adapters, which already stop on it, so the run is not
     enforced twice with different meanings.
2. **Enforced at the two places where cost happens.**
   - The model client wrapper charges tokens and AUD after each call.
   - The tool executor wrapper charges a tool call before it runs.
   - A trip raises `StopRun("budget: <limit>: <numbers>")`, so the run ends `stopped` with its
     partial transcript, and the run record carries the reason and the budget status.
3. **Loop detection from what actually happened.** A call made no progress if its result
   equals the last result of the same call. A model reply with tool calls is judged at the tool,
   not twice. This catches identical repeats, steps without progress, and A-B-A-B oscillation.
   It can be tuned per spec (`budgets.loop`).
4. **`on_exceed: stop | summarise`.** Summarise makes one capped model call (300 tokens),
   outside the exhausted budget, to say what was achieved and what is left.
5. **Circuit breakers shared by every run in the process.**
   - A tool's circuit opens after 5 dependency failures. Denials and bad arguments don't count.
   - A model role's circuit opens after 3 failures, and stops the run with the reason.
   - After the cooldown, one trial call is let through (half-open).
6. **The gateway as the hard ceiling.** One LiteLLM team for the platform; the team budget is
   the sum of the role caps, and every role key is in the team. In-process budgets can
   overshoot by one call, the gateway cannot.
7. **Reconciliation as a check, not a dashboard promise.**
   - Every call's cost is stored as LiteLLM reported it (USD), along with the key that paid.
   - `just cost-reconcile` compares this with LiteLLM's spend logs per key.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Budgets only at the gateway (LiteLLM keys per run) | Exact and cross-process, but a key per run is heavy, and cannot count tool calls, wall-clock time or delegation depth |
| Count `max_steps` in the controller too | Two different step definitions would trip at different points; adapters already enforce it |
| Loop detection on raw call signatures only | Flags legitimate polling and varied searches; progress (a changed result) separates work from a loop |
| Per-run circuit breakers | Each run would rediscover the same outage; shared breakers spare the others |
| Dashboards from traces only | Traces can be sampled; the ledger holds every call and reconciles against the gateway |

## Consequences
- Budgets live in process memory. A run split across the API and the Temporal worker is
  metered per process, and the gateway's team budget covers both.
- A budget can be exceeded by at most the call that crossed it, because cost is known only
  afterwards.
- After a restart, a paused run's budget starts again from zero.

## Verification
- `services/agent-api/tests/test_budgets.py`, 6 tests through the run API, with models that
  never answer:
  - identical calls stop at the 3rd call, with `loop: repeated call: calculator…`;
  - at AUD 0.03 per call, the run stops at AUD 1.0200 against a limit of 1.00 (34 calls);
  - `max_tool_calls`, `max_calls_per_tool` and `max_tokens` are each named in the run record;
  - summarise adds a summary.
- `libs/agent-runtime/tests/test_budget.py`: the contracts plus cost detail, per tool, wall
  clock, depth, the parent stopping children, no-progress and oscillation.
- `libs/agent-runtime/tests/test_circuit.py`: open, half-open and closed; denials not counted;
  the model circuit stops the run.
- Cost: with the stand-in LiteLLM v1.105.1 (stand-in prices), 3 calls through one key were
  recorded by the platform as USD 0.00015, and LiteLLM's spend logs report USD 0.00015.
  Provisioning creates the team and puts keys in it, verified live.
