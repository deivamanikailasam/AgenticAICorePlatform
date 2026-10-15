# ADR 0013: agent.yaml v1 layout, secret references and safe-by-default budgets

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 03
- **Deciders:** repository owner

## Context
The spec's minimal `agent.yaml` "shows the intent" and leaves the exact schema to Step 3. Its
`graph:` block lists nodes and then `edges:` inside the same list, which is not valid YAML.
We also need rules for secrets, budgets and versioning.

## Decision
1. **Layout.** `graph: {entry?, nodes: [...], edges: [...]}`. Edges accept `"a -> b"`,
   `"a -> b: <outcome>"` or `{from, to, when}`. The draft layout (bare node list, top-level
   edges, tools as strings) is accepted as `spec_version: 0` and migrated to 1 on load.
2. **Schema source.** Pydantic v2 models are the single source; the JSON Schema
   (draft 2020-12) and the reference page are generated and checked in CI.
3. **Interpolation.** `${VAR}`, `${VAR:-default}`, `${env:VAR}` are resolved at load time.
   `${secret:<path>#<key>}` is never resolved by the loader and is only allowed under
   `tools[].config`; tools resolve it from OpenBao at call time (Step 29).
4. **Budgets are mandatory.** Every spec sets `max_steps` and `max_wall_seconds`; specs that call
   models must also set `max_tokens` and `max_cost_aud`. Delegation depth (2) and fan-out (8) have
   defaults.
5. **Cycles need a bound.** A cycle is valid only if one of its nodes sets `max_iterations`.
6. **Risk tier drives requirements.** `high` requires an eval suite; `medium`/`high` with tools
   warns when no guardrails are enabled.
7. **Model roles.** Nodes name a role defined under `models:` (e.g. `planner`), never a raw model
   id, so the gateway alias can change without editing nodes.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Accept the draft layout as v1 | Not valid YAML |
| Hand-written JSON Schema | Drifts from the code that enforces it |
| Resolve secrets at load time | Secrets would sit in the compiled plan, traces and checkpoints |
| CUE or Jsonnet specs | More powerful, but a new language for application teams |
| Optional budgets | Violates principle 5 (safe by default) |

## Consequences
Clear, reviewable specs and early, complete error reports. Schema changes require a new
`spec_version` and a migration function with a test.

## Verification
`libs/agent-spec/tests/test_loader.py` covers each rule; `just validate agents/` passes.
