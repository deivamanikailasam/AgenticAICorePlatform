# Commit the stand-in's test settings so CI can use them

**Step:** 01 · **Commit:** fix(ci): track the stand-in env file ignored by .gitignore · **Date:** 2026-10-15

## What changed
`tests/stack/standin.env` (addresses and throwaway passwords for the CI stand-in) was being
skipped by the `*.env` rule in `.gitignore`, so the CI integration job could not load it.
An explicit exception now tracks that one file.

## Why
The rule that keeps real `.env` files out of git is right; it just also caught a file whose
values are deliberately public test settings, like a dummy key cut for a training lock.

## How to check it
`git ls-files tests/stack` lists `standin.env`; the CI integration job passes.

## Risks and follow-ups
The file must only ever hold stand-in values. The secret scan still runs over it.
