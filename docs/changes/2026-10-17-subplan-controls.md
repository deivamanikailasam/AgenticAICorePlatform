# A nested spec runs under its parent's controls

## What changed
A `subgraph` node's child run now gets the parent's controls:
- the delegation policy check (OPA `agent.delegate`);
- the worker factory;
- the admission gate (registry approval in staging and prod);
- budgets, memory, artefacts and tracing.

Before, the child was built with only the model and the tool executor.

## Why
Step 40's research-report app nests its team in a workflow, so that publishing waits for an
approval outside the team. Without the parent's worker factory a nested team failed, and
without its delegation policy a nested team would delegate unchecked. Tool calls were already
covered, because the child shares the parent's authorising tool executor.

## How to check it
`uv run pytest libs/agent-compiler/tests/test_compiler.py -k nested`. The child is built with
exactly the parent's delegation policy, workers, admission gate and budgets. The test fails
without the change.

## Risks and follow-ups
None: the change only passes on what the parent already had.
