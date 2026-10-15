# Add SeaweedFS S3 to the test stand-in

**Step:** 06 · **Commit:** test(stack): add SeaweedFS 4.09 S3 to the Project 0 stand-in · **Date:** 2026-10-15

## What changed
The CI stand-in now runs SeaweedFS 4.09 with its S3 API on `127.0.0.1:8333` (digest-pinned,
with a healthcheck), the same kind of object store Project 0 provides. New settings:
`P0_S3_ENDPOINT`, `AGENT_ARTEFACT_BUCKET` and, for the stand-in only, throwaway S3 credentials
and the `AGENT_DB_DSN` of the `agent` database.

## Why
Step 6 spills large tool outputs to object storage, and that path must be tested against a real
S3 API rather than a fake.

## How to check it
`just standin-up && curl -s -o /dev/null -w "%{http_code}" 127.0.0.1:8333/` prints `200`.

## Risks and follow-ups
SeaweedFS needs about 25 seconds to elect its master before S3 answers; the healthcheck waits.
On the Mac, the S3 credentials come from `secrets/dev.enc.env` (VERIFY-ON-MAC).
