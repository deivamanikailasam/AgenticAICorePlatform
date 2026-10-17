# Service images include the platform's policy data

## What changed
`infra/compose/service.Dockerfile` now copies `policies/agent/config/data.json` into the image.

## Why
Since 320926a, the `agent-tools` wheel force-includes that file, so the agent projects of
Step 39 get it. Hatchling refuses to build a package whose force-included file is missing, and
the image copied only `policies/openfga/`. The integration job's `just up`, which builds the
service images, therefore failed on 0476e7f. The images workflow uses the same Dockerfile. The
file is also what the in-process approval router reads, so the image needs it in any case.

## How to check it
Copy what the Dockerfile copies into an empty directory and run
`uv sync --frozen --no-dev --package agent-api`: it installs. Without the new line it fails in
hatchling.

## Risks and follow-ups
None. The file is data with no secrets: approval routes and kill switches.
