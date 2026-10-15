# Publish the agent.yaml schema and reference page

**Step:** 03 · **Commit:** docs(spec): generate the agent.yaml JSON Schema and reference page · **Date:** 2026-10-15

## What changed
`just spec-reference` writes two files from the Pydantic models:
`docs/reference/agent-spec.schema.json` (JSON Schema draft 2020-12, usable by editors for
autocomplete and inline errors) and `docs/reference/agent-yaml.md` (every field, type,
whether it is required and its default). `just check` fails if either is stale.

## Why
Like the form guide printed next to a government form: generated from the form itself, so the
guide and the form always agree.

## How to check it
`just spec-reference-check` prints `spec-reference: up to date`. In VS Code, add
`# yaml-language-server: $schema=../../docs/reference/agent-spec.schema.json` to the top of a
spec to get autocomplete.

## Risks and follow-ups
None.
