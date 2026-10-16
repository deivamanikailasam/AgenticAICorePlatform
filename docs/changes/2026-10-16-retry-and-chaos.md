# Retry a failed run from its checkpoints; the chaos suite

**Step:** 33 · **Commit:** feat(agent-api): retry failed runs from checkpoints; chaos tests · **Date:** 2026-10-16

## What changed
- **`POST /v1/runs/{id}/retry`** (owner only; the run must be `failed`; needs a checkpointer). It continues a failed run from its checkpoints:
  - Nodes that completed are answered from the checkpoints, so their side effects are not repeated.
  - Only the failed attempt's outcomes are dropped.
  - The run keeps its id, so tool idempotency keys (`<run>:<node>`) still guard any write that was in flight.
- **Chaos suite** (`services/agent-api/tests/test_chaos.py`). It drives the real run API, gateway client and executors, faking only the network (an `httpx` transport) and the clock:
  - **Model timeout:** retried by the gateway client, and the run carries on.
  - **Gateway down:** runs fail fast with the reason. After 3 failures the model circuit opens, and the next run stops at once without calling the gateway. Once the gateway is back and the cool-down has passed, `retry` takes the first run on to its approval without repeating its completed tool step.
  - **Retry rules:** only the owner may retry, and only a failed run.
  - **Slow tool:** a call that hangs is cut at the tool's 0.2 s limit, the agent is told "timed out" as a result, and it finishes. Five timeouts open the tool's circuit; the sixth call is refused without reaching the tool.
  - **Gateway fallback** (`-m integration`): the stand-in LiteLLM has a model that always fails (`local-qwen-flaky`) with a fallback to `local-qwen-fast`. An agent using it completes, and every answer is served by the fallback deployment.
- **Killed worker:** already covered where processes really die. `test_durable_runs.py` kills a worker with SIGKILL mid-run and resumes without repeating the write; `test_api.py` covers a running run resuming after a restart.
- **`just test-chaos`** runs all of these.

## Why
Step 33's "Done when": "chaos tests show correct resume and fallbacks". Before this change, a run that failed because the gateway was down could only be started again under a new run id, which would repeat its completed writes.

## How to check it
`just test-chaos` (9 passed locally, 25 s). With the stand-in, `uv run pytest -m integration services/agent-api/tests/test_chaos.py`.

## Risks and follow-ups
Retry re-runs the failed node in full. For a ReAct node, that means its model turns, and any tool calls inside it, run again. Write tools inside agent nodes still rely on their idempotency keys and approvals (Steps 6 and 15).
