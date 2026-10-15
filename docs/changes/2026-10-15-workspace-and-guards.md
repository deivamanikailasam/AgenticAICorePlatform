# Set up the workshop: workspace, task menu and safety checks

**Step:** 01 · **Commit:** chore(repo): add uv workspace, justfile, pre-commit and guard scripts · **Date:** 2026-10-15

## What changed
The repository is now a Python 3.12 "uv workspace": one place that holds many small packages
with a single lock file, so everyone installs exactly the same versions. A `justfile` gives a
menu of commands (`just check`, `just health`, `just up`…). Four small guard scripts run on
every check:

- **compose check**: every container port listens only on 127.0.0.1, every image is pinned by
  digest, long-running services have memory limits, nothing runs privileged.
- **secret scan**: refuses anything that looks like a key or token, and any `*.enc.*` file that
  is not really encrypted.
- **change-entry check**: every commit must carry a plain-English note like this one.
- **health**: reaches each Project 0 service Project A needs and prints a table.

## Why
Like a workshop with labelled drawers and a safety checklist by the door: the same tools in the
same place for everyone, and the dangerous mistakes (an open port, a leaked key) are caught
before they leave the building.

## How to check it
`just check` prints `All checks passed!`, `8 passed`, `compose-check: … 0 problem(s)` and
`secret-scan: … 0 finding(s)`.

## Risks and follow-ups
The secret scan uses patterns, so it can miss unusual secrets; GitHub secret scanning is the
second net. Default service addresses in `.env.example` are assumptions until checked against
Project 0's compose file on the Mac.
