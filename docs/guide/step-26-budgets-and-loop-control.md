# Step 26 · Budgets and loop control

## 1. At a glance

| | |
|---|---|
| **Goal** | No agent can spend, call or wait without limit; runaway agents stop with a clear reason |
| **Time** | About two days |
| **You end up with** | A budget controller and loop detector in every run; circuit breakers; `on_exceed: stop \| summarise`; a LiteLLM team budget over the role keys; a persisted cost ledger and `just cost-reconcile` |
| **New words** | budget tree, trip, loop detection, progress, circuit breaker, half-open, reconciliation |

## 2. The big idea

Agents decide for themselves how many steps to take, so they need hard edges:
- money, tokens, time and tool calls run out;
- repeating the same thing without getting anywhere is noticed;
- a broken tool or model is left alone for a while instead of being hammered.

When an edge is hit, the run stops politely. It keeps what it did so far and says exactly which
edge it hit.

> *Analogy:* a prepaid card with a daily limit, a smoke alarm and a fuse box. The card stops
> spending at the limit, the alarm notices something going round in circles, and the fuse cuts
> off a faulty appliance before it takes the house down. The bank (the gateway) has its own
> limit too, in case everything else fails.

## 3. What we're doing

1. Build the budget controller: a tree of budgets for steps, tokens, AUD cost, wall-clock time,
   tool calls (in total and per tool) and delegation depth.
2. Build the loop detector: identical repeats, steps without progress, A-B-A-B oscillation.
3. Charge every model call and tool call in every run, and stop gracefully with the reason.
4. Add circuit breakers for failing tools and models.
5. Put the role keys in one LiteLLM team with a budget, record every call's cost, and check
   that it matches the gateway.

## 4. Why we're doing this

A looping agent can burn through money and rate limits in minutes. A cascade of agents can
multiply that (OWASP ASI08). A failing tool, retried by every run, turns a small outage into a
big one.

> *Watch out:* a limit that only logs a warning is not a limit. Every limit here stops the run,
> and the stop is visible in the run record.

## 5. Key ideas

- **Budget tree.** A team worker's budget sits under its team's budget. Its spending counts
  for both, and when the team's budget trips, every worker stops.
- **Charged where cost happens.** The model client is charged after each call, with the cost
  the gateway reported. The tool executor is charged before each call.
- **Trip.** The run ends `stopped`, with its partial transcript, the budget status and a reason
  such as `budget: max_cost_aud: AUD 1.0200 over the limit of 1.0`.
- **Progress.** A call made progress if its result differs from the last time the same call was
  made. Repeating a call with the same result is not progress.
- **Stop or summarise.** `on_exceed: summarise` adds a short summary of the partial result, from
  one capped model call outside the exhausted budget.
- **Circuit breaker.** After repeated dependency failures (timeouts, errors, rate limits; not
  denials), calls are refused at once for a cooldown. Then one trial call is let through.
- **Gateway ceiling and reconciliation.** One LiteLLM team caps the platform, and each key caps
  its role. Every call's cost is stored as the gateway reported it, so `just cost-reconcile`
  can compare the two sides.

## 6. How to do it

```yaml
budgets:
  max_steps: 20
  max_wall_seconds: 120
  max_tokens: 50000
  max_cost_aud: 0.50
  max_tool_calls: 30
  max_calls_per_tool: { erp__post_payment: 1, search: 10 }
  max_delegation_depth: 2
  on_exceed: summarise          # or stop (default)
  loop: { repeat: 3, window: 8, no_progress: 6, oscillation: 3 }
```

```bash
just provision-litellm                # team agent-platform + role keys in it
just migrate                          # agent_runtime.usage_ledger
just cost-reconcile --start 2026-10-01 --end 2026-10-15 --wait 90
```

## 7. Benefits

- A runaway agent costs a bounded amount, and you can see why it stopped.
- Teams cannot multiply spending past their own budget.
- Outages in one tool or model don't drag every run down with them.
- Cost reports can be checked against the gateway instead of trusted.

## 8. Drawbacks and risks

- A budget can be exceeded by the one call that crossed it. The gateway's limit is the hard
  ceiling.
- Budgets are held per process. A run split across the API and the worker is metered in each,
  and the gateway team budget covers both.
- After a restart, a paused run's budget starts again.
- Tight loop settings can stop legitimate polling. Tune `budgets.loop` per agent.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Stop | Stop and summarise | People read the partial result and need a quick account of it |
| In-process budgets plus gateway ceiling | A gateway key per run | You need exact cross-process spend per run and can afford a key per run |
| Progress-aware loop detection | Repeat counting only | Agents never poll, so any repeat is a loop |
| Shared circuit breakers | Retries only | Dependencies fail briefly and recover in seconds |

## 10. Which approach fits which situation

- **Customer-facing assistant:** a cost and wall-clock limit, and `stop`; show the reason to
  the user.
- **Back-office batch:** `summarise`, so reviewers see what was done before the limit.
- **Agent that writes to systems:** `max_calls_per_tool` of 1 on the writing tool.
- **Teams:** keep `max_delegation_depth` small and put the team budget on the team spec.

## 11. Common mistakes

- Setting only `max_steps` and no cost or time limit.
- Counting a policy denial as a tool failure, which opens circuits for the wrong reason.
- Reading cost dashboards without reconciling them.
- Running `cost-reconcile` straight after traffic without `--wait`. LiteLLM writes spend in
  batches.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| A deliberately looping agent stops within its limits | ✅ | `test_budgets.py`, through the run API with models that never answer: identical calls stop at the 3rd call; costly calls stop at the call that crossed AUD 1.00 (AUD 1.0200, 34 calls); tool-call, per-tool and token limits each stop the run |
| The run record shows which limit tripped | ✅ | `stop_reason` is `budget: <limit>: <numbers>` or `loop: <reason>`, and the output carries the budget status and partial transcript |
| Cost dashboards match the gateway's accounting | ✅ (stand-in) | Integration test: 3 calls through one key, platform ledger USD 0.00015 = LiteLLM spend logs USD 0.00015; `just cost-reconcile` compares per key. VERIFY-ON-MAC against Project 0's LiteLLM with real prices |
| Budgets: steps, tokens, AUD, wall clock, tool calls per tool, delegation depth; hierarchical | ✅ | `test_budget.py`: contracts, per-tool, wall clock without reports, depth, parent trip stops children |
| Hierarchical budgets mapped to LiteLLM virtual keys | ✅ | Provisioning creates team `agent-platform` (budget = sum of role caps) and puts every role key in it; checked against the stand-in |
| Loop detector: repeats, no progress, oscillation | ✅ | `test_budget.py` and the API loop test |
| Circuit breakers on tools and models | ✅ | `test_circuit.py`: open, half-open and closed; denials not counted; the model circuit stops the run with the reason |
| Graceful stop; stop + summarise alternative | ✅ | Partial transcript kept; `on_exceed: summarise` adds the summary |
| ADR | ✅ | ADR 0037 |

## 13. Recap

Every run now has a budget tree, a loop detector and circuit breakers around its tools and
models. When a limit trips, the run stops with its partial result and names the limit. The
gateway's team budget is the ceiling behind it all, and every call's cost is recorded so it can
be reconciled against the gateway.
