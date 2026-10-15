# Templates can use `${node.field}` again

**Step:** 15 · **Commit:** fix(spec): leave run-value references in templates to the executors · **Date:** 2026-10-15

## What changed
The spec loader replaces `${VAR}` with environment variables. It also tried to treat
`${validate.total}`, a reference to an earlier step's result inside a node template, as an
environment variable and rejected it. Lower-case dotted references are now left alone for the
node executors to fill in at run time; environment variable names are upper case, so the two
cannot be confused.

## Why
The executors and the Step 13 and 14 chapters document `${node.field}` in templates, but a spec
using it would not load. Found while writing the Step 15 prompt-chain tests.

## How to check it
`uv run pytest libs/agent-spec` — includes a spec with both `${validate.total}` (kept) and
`${GREETING}` (replaced), and a malformed reference that is still refused.

## Risks and follow-ups
None known: upper-case and `env:` references behave exactly as before.
