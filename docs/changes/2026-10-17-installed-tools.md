# The run API loads a team's installed tools

## What changed
`Assembly.registry` (agent-api) now accepts a `local://` tool ref that is not one of the
platform's own. It does so only when a `tool` component installed in the image (entry point
`agent_platform.components`, `tool.<name>`) declares exactly that ref in its `ToolSpec`.
- Test doubles from `agent_contracts.dummies` never count.
- A component that needs configuration to build is skipped.
- Any other ref is refused before the run starts, as before.

## Why
Step 39's SDK lets a team write tools in its own package and install it into its image. Without
this, the run API refused every such tool.

## How to check it
`uv run pytest services/agent-api/tests/test_installed_tools.py`: the installed tool loads by
its ref; a ref no installed tool declares is refused.

## Risks and follow-ups
Only code built into the image can be loaded, and only the tools the spec lists. Each call still
passes OPA, guardrails, budgets, audit and the approval rule for irreversible tools.
