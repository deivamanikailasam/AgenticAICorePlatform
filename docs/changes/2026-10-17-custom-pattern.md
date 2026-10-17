# Specs can use a team's own pattern builder

## What changed
- `pattern: custom` is a new value in `agent.yaml`. It names the builder in
  `components.pattern_builder`, which must be installed under the `agent_platform.components`
  entry-point group as `pattern_builder.<name>`.
- Validation refuses `pattern: custom` without `components.pattern_builder`. It already refused a
  component name that isn't installed.
- The compiler builds the plan with that builder, then treats it like every other plan: agent
  nodes expanded, approval gates inserted in front of tools that require approval, plan checked
  and hashed. `metadata.pattern` stays `custom`.
- `docs/reference/agent-yaml.md` and the JSON Schema are regenerated.

## Why
Step 39 asks for an SDK in which teams write their own patterns. Until now the pattern list was
closed, so a new pattern had to be added to the platform itself.

## How to check it
`uv run pytest libs/agent-compiler/tests/test_compiler.py -k custom`: a team builder that calls
an irreversible tool directly still gets the approval gate in front of it, and a missing or
unknown builder is refused.

## Risks and follow-ups
A custom builder is code the team owns. The platform's guarantees come from what runs after it
(approval insertion, OPA on every tool call, budgets), not from trusting the builder.
