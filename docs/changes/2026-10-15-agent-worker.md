# agent-worker: the Temporal worker as its own service

**Step:** 19 · **Commit:** feat(agent-worker): serve durable agents from a Temporal worker service · **Date:** 2026-10-15

## What changed
- New service `services/agent-worker`. It loads `agents/`, compiles every
  `durability: temporal` spec and builds each node runner with the run API's `Assembly`: model
  gateway, tools under OPA and audit, and functions.
- It serves each plan on the `agent-platform` task queue under the handle clients use
  (`<name>@<version>`).
- Without `P0_S3_ENDPOINT`, values stay inline; with `P0_S3_ENDPOINT` set, large values go
  to Project 0's S3 by reference, and the keys come from the secret store.
- In compose, `temporal` is no longer behind a profile. `just up` starts `temporal` and
  `agent-worker` together with the gateway and the API, as the spec's start-services step
  lists.

## Why
The spec's layout puts the Temporal worker in `services/agent-worker`. Workflows only decide;
the worker is where model and tool calls run, so it scales and restarts independently of the
API.

## How to check it
`just up`, then `docker logs agent-platform-agent-worker-1`. Then run
`uv run pytest services/agent-worker -m "integration or not integration"`: a client with no
runner starts a run, and the worker executes it.

## Risks and follow-ups
- No agent in `agents/` uses `durability: temporal` yet, so the worker serves none by default.
- The run API still runs specs in-process. Streaming per-node AG-UI events from a Temporal run
  back to the API is not built yet.
- On-behalf-of tools need delegated credentials in the worker (Step 29).
