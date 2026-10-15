# Let the formatter own line length

**Step:** 03 · **Commit:** chore(lint): let ruff format own line length and format docs code blocks · **Date:** 2026-10-15

## What changed
The lint rule that flags lines over 100 characters (E501) is switched off; `ruff format` already
wraps code at 100 characters, and the remaining long lines were readable error messages and
docstrings. Ruff 0.17 also formats Python code blocks inside Markdown, so the how-to's example
code is now formatted the same way as the source code.

## Why
Two tools enforcing the same rule differently produce churn without improving readability.
This follows ruff's own recommendation when the formatter is in use.

## How to check it
`just lint` passes; `uv run ruff format --check .` reports every file formatted.

## Risks and follow-ups
Very long lines are still possible inside strings; reviewers should flag any that hurt reading.
