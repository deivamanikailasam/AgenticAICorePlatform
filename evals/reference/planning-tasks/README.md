# Reference tasks for comparing agent patterns (Step 14)

Three multi-step tasks (calculator and word counter) used to compare `react`, `plan_execute`,
`rewoo`, `reflection` and `evaluator_optimiser` on the same work: solved count, model calls,
tool calls, tokens and cost (AUD, from the gateway's usage).

- `question` and `expect` are what a real model sees and must answer.
- `react` and `plan` are only used by the deterministic stand-in model in CI, which replays a
  correct trajectory so the harness and metering can be tested. CI numbers describe that
  stand-in, not any model, and are never reported as results.

Run against Project 0's local model on the Mac (VERIFY-ON-MAC):

    just compare-patterns

The table it prints names the model alias, the dataset and its size (3 tasks). Only those numbers
may be quoted, together with the dataset size.
