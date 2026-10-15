# Teach the platform to read and check agent.yaml

**Step:** 03 · **Commit:** feat(spec): add agent.yaml models, loader, semantic validation and migrations · **Date:** 2026-10-15

## What changed
A new library, `agent-spec`, reads `agent.yaml` files:

- **Schema** in Pydantic: name, version, owner, risk tier, autonomy, pattern, model roles,
  runtime, budgets, tools (with approvals), memory, guardrails, graph (nodes and edges),
  sub-agents, component choices, evals, interop and labels.
- **Loader**: replaces `${VAR}` / `${VAR:-default}`; keeps `${secret:path#key}` *unresolved*
  and only allows it under `tools[].config`, so credentials never reach prompts.
- **Semantic checks** a schema can't express: unknown tools and model roles, edges to missing
  nodes, unreachable nodes, cycles without a loop bound, router edges to unknown routes, missing
  token/cost budgets when models are used, unknown guardrails and components, high-risk specs
  without evals.
- **Migrations**: `spec_version` 0 (the draft layout with a bare node list) upgrades to 1
  automatically; specs newer than the platform are refused.
- **Readable errors**: every problem is reported at once, with its line number.

`just validate` checks every spec under `agents/` and runs in `just check`. Two example specs
ship: `hello-workflow` and `invoice-triage` (the spec's example in the v1 layout).

## Why
`agent.yaml` is the contract between an application team and the platform, like a building
permit application: it has to be complete and consistent before anyone starts building, and
the clerk should list every missing item at once, not one per visit.

## How to check it
`just validate` prints `validate: 2 file(s), 0 failed`.
`uv run agent-spec validate libs/agent-spec/tests/fixtures/invalid` lists each problem as
`file:line: path: message`.

## Risks and follow-ups
The spec's example wrote `edges:` inside the node list, which is not valid YAML; v1 nests
`graph: {nodes, edges}` and the migration accepts the draft layout as `spec_version: 0`
(ADR 0013). The runtime adapter name is checked when compiling (Step 4), not when loading.
