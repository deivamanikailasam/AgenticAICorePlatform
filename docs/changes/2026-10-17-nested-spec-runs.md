# A nested spec runs as its own agent

## What changed
- A `subgraph` node now runs its spec through the same path as a team worker:
  - its own identity and versions in the audit trail;
  - its own `invoke_agent` span;
  - the registry gate;
  - when the run API assembles it, its own model roles and only its own tools under policy.
- The parent's delegation policy, worker factory and budgets still apply.
- Its child run is `<run>/<node>`, like team workers' `<run>/<worker>-<n>`. Before it was
  `<run>.<node>`, which the audit stamper, budgets and the eval observer didn't recognise as
  part of the run.

## Why
Step 40's research-report app nests its team inside a workflow. Running it on the parent's model
client stopped the team, and its delegations and tool calls were left out of the run's audit
view and its evaluation.

## How to check it
`uv run pytest libs/agent-compiler/tests/test_compiler.py -k nested`: the child is assembled
like a worker and gets the parent's controls. Step 40's app tests (`test_team_api`,
`test_audit_trail`, `test_telemetry`) check the audit view and the trace tree.

## Risks and follow-ups
A nested spec now needs to be approved in the registry in staging and prod, like any worker.
