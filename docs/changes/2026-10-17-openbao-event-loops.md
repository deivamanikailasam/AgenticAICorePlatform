# OpenBao client: one HTTP pool per event loop

## What changed
- `OpenBaoClient` (`libs/agent-tools/src/agent_tools/openbao.py`) keeps one HTTP client per
  event loop instead of one for its whole life. It drops clients whose loop has closed.
- A regression test, `test_client_survives_successive_event_loops`, resolves a secret against a
  real keep-alive HTTP server in two successive `asyncio.run()` calls.

## Why
In the deploy-e2e run on 26dabb2, Argo CD synced dev but the application stayed Degraded: a pod
crashed with `RuntimeError: Event loop is closed`. With Kubernetes auth, the services resolve
their `${secret:...}` connection strings with `asyncio.run()` before they start serving in a new
loop. The process's cached resolver then reused a keep-alive connection bound to the first,
closed loop. Compose never hit this, because there the environment resolver was used.

## How to check it
`uv run pytest libs/agent-tools/tests/test_openbao.py -m "not integration"`. The new test fails
with "Event loop is closed" without the change.

## Risks and follow-ups
A long-lived loop still shares one pool, as before. The deploy-e2e result is confirmed after the
overlays are re-pinned to this commit's images.
