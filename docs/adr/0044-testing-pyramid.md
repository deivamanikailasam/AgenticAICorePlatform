# ADR 0044: The testing pyramid: offline unit tests, cassettes at the edges, MCP contracts, chaos through the real code paths

- **Status:** Accepted
- **Date:** 2026-10-16
- **Step:** 33
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 33 asks for:
- unit tests with a scripted fake model;
- contract tests per interface;
- record-and-replay (cassettes) of model and tool calls for deterministic integration tests;
- tool contract tests against MCP servers;
- end-to-end tests on the compose stack;
- chaos tests: kill worker, slow tool, model timeout, gateway down.

Done when:
- `just test` runs in under 5 minutes with no network;
- replayed runs are byte-identical;
- chaos tests show correct resume and fallbacks.

Several layers already existed: the scripted model (Step 2), 30 contract suites (Steps 2–29),
end-to-end integration tests on the stand-in (Steps 1–31) and SIGKILL resume tests (Step 18).

## Decision
1. **No network, enforced.** The root `conftest.py` refuses non-loopback connections and name
   lookups in every test not marked `integration`, `mac`, `sandbox` or `microvm`. It also clears
   proxy variables, since a loopback proxy is a way out. "No network" becomes a property the
   suite checks, not a hope.
2. **Cassettes at the edges only** (`libs/agent-testing`).
   - Model and tool calls are recorded and replayed. Policy, guardrails, budgets, approvals and
     audit run live, so replays still test the platform.
   - Requests are matched by fingerprint, with per-run metadata ignored.
   - A request with no recording raises an error naming where it differs, so behaviour changes
     can't pass silently.
   - Byte-identical means the canonical transcript of calls (requests and responses) is equal.
     Run ids and timestamps legitimately differ between runs.
3. **An MCP contract per server** (`agent_tools.mcp.contract`), run through the real client and
   executor:
   - discovery, namespacing and schemas;
   - one example call per tool, plus refusal of bad arguments;
   - platform-declared side effects;
   - allowlist enforcement.
4. **Chaos through the production code paths.** Only the network (an `httpx` transport) and the
   clock are faked; the gateway client, circuit breakers, executors, checkpoints and run API are
   real. Process kills stay in the existing SIGKILL tests. LiteLLM's fallbacks are tested against
   the stand-in gateway itself, not re-implemented in the platform.
5. **What the chaos tests found was fixed, not worked around:**
   - streamed model calls had no retries;
   - a failed run could not continue (now `POST /v1/runs/{id}/retry`, from checkpoints);
   - MCP calls to tools off the allowlist reached the server;
   - LiteLLM crashed when its cost-map download failed;
   - a promtool test depended on rule-group order.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| VCR.py / pytest-recording at the HTTP layer | Records bytes on the wire, so replays depend on HTTP details (headers, retries, ids) and can't cover in-process tools; recording at the `ModelClient`/`Tool` interfaces matches what the platform contracts on |
| pytest-socket for the network guard | Equal for blocking; the in-repo guard also clears proxy variables and allows loopback by default, which the stand-in fakes need, without another dependency |
| Toxiproxy or Chaos Mesh | Real network faults, worth it on a cluster (Step 37). Here the faults are deterministic and in-process, so the tests are fast and never flaky |
| A platform-side model fallback list | LiteLLM already applies fallbacks (Project 0's config); a second layer would hide which one acted. `platform.yaml` `models.fallbacks` stays informational |

## Consequences
- **Measured here:** `just test` ran 913 passed and 21 skipped in about 65 s locally, with the
  guard on. The CI check job, including lint, types, policy and config, took about 2 min 20 s
  on run 59.
- **Cassettes pin behaviour.** An intended change means re-recording (`just record-cassettes`)
  and reviewing the diff. A cassette holds whatever the recorded run saw, so record only
  synthetic or stand-in data.
- **Retry scope.** Retry re-runs the failed node in full. Writes inside agent nodes rely on their
  idempotency keys and approvals.
- **Not yet under the MCP contract:** rag-core (Project 0, VERIFY-ON-MAC) and the platform's MCP
  gateway, which needs Keycloak tokens.
