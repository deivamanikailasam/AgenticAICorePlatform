# Platform wheels carry the files they read

## What changed
- `agent-tools` now builds its wheel with the platform's policy data
  (`policies/agent/config/data.json`), which the in-process approval router reads.
- `agent-spec` now builds its wheel with the default `platform.yaml`.
- Both still read the repository's file when it is there, as in this workspace's editable
  installs. Otherwise they read the copy inside the package (`<package>/_platform/`). Hatch's
  `force-include` puts the copy there.

## Why
Agent projects from the Step 39 template install the platform from git, as wheels. There,
`parents[4]` points into the virtual environment, and the first `agentctl run` failed with
`FileNotFoundError: .../policies/agent/config/data.json`.

## How to check it
`just template-e2e --local` (the platform built as wheels from this checkout): the generated
project's `agentctl run` and `agentctl eval` succeed. A wheel from
`uv build --wheel libs/agent-tools` contains `agent_tools/_platform/policy-data.json`.

## Risks and follow-ups
The copies are taken at build time from the same commit, so they can't drift from the source.
