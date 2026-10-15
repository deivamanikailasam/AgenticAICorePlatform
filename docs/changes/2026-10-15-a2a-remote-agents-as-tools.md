# Remote A2A agents as allowlisted tools

**Step:** 24 · **Commit:** feat(agent-tools): call allowlisted remote A2A agents as tools · **Date:** 2026-10-15

## What changed
- `platform.yaml` has a new `a2a:` section. `a2a.agents` is the allowlist of remote agents:
  name, URL, the pinned public keys that must sign each agent's Agent Card, the binding
  (JSON-RPC, HTTP+JSON or gRPC), side effect (default `write`), credentials and timeout.
  `a2a.publish` configures the gateway that the next commit adds.
- New `agent_tools.a2a.A2aRemoteAgentTool`. It checks the remote agent's Agent Card signature
  (ES256) against the pinned keys, then sends the message with the official `a2a-sdk` 1.2.1
  client. It returns the final task state and text as data. Verified cards are cached for
  5 minutes.
- Credentials come from the agent's `auth` setting, as HTTP headers or gRPC metadata:
  - a bearer token from the secret store;
  - client credentials;
  - the user's token exchanged for the remote audience (`on_behalf_of`). This never falls back
    to a service identity.
- The run API's assembly resolves `a2a://<name>` tool refs. A name that is not on the allowlist
  fails when the run is built.
- Dependencies: `a2a-sdk[http-server,signing]==1.2.1` and `grpcio==1.84.0`. The mypy config
  ignores missing `grpc` stubs.

## Why
The spec's Step 24 requires that remote A2A agents can be called as tools, and that an unsigned
or unlisted remote agent is refused. Listing each agent with pinned keys means an attacker who
controls DNS, or the card URL, cannot impersonate it (ASI07, ASI04). The calls go through the
normal tool executor, so OPA, grants, rate limits and audit apply unchanged. The remote reply is
a tool output: untrusted data, never instructions.

## How to check it
`uv run pytest libs/agent-tools/tests/test_a2a_tool.py services/agent-api/tests/test_a2a_refs.py`

These tests cover:
- a signed agent answering with the configured token;
- an unsigned card refused before any message is sent;
- a rogue key and an unpinned kid, both refused;
- `on_behalf_of` refused when there is no user token;
- an unlisted agent refused at build time;
- a listed agent registered as `a2a://pyd-helper`.

## Risks and follow-ups
- Card keys are pinned by hand in `platform.yaml`. Rotating a partner's key means a config
  change; JWKS (`jku`) is not trusted on purpose.
- Plain-text gRPC is used only for loopback targets.
