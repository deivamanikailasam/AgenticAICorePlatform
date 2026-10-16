# Step 33 · Testing pyramid

## 1. At a glance

| | |
|---|---|
| **Goal** | Tests that are fast, repeatable and offline at the bottom, realistic at the top, and that prove the platform behaves when things break |
| **Time** | About a day and a half |
| **You end up with** | A no-network guard on `just test`; `libs/agent-testing` cassettes; `agent_tools.mcp.contract`; the chaos suite (`just test-chaos`); `POST /v1/runs/{id}/retry`; five fixes the new tests found |
| **New words** | testing pyramid, fake, contract test, cassette, record and replay, chaos test, circuit breaker, fallback |

## 2. The big idea

Agents fail quietly. A changed prompt or a flaky dependency often produces a plausible wrong
answer, not an error. So the tests need three things:

- **Many fast, exact tests at the bottom.** These are unit tests with a scripted fake model, and
  contract tests saying what every implementation of an interface must do.
- **Realistic tests that are still repeatable in the middle.** Cassettes record real model and
  tool calls once, then replay them anywhere, offline, identically.
- **A few slow tests at the top.** These run end to end on the stand-in stack, plus chaos tests
  that break things on purpose.

> *Analogy:* a flight simulator. Pilots practise hundreds of routine landings in the simulator
> (fast, cheap, repeatable). They replay recorded real flights to see exactly what happened. And
> they rehearse engine failures, because you can't wait for one to happen in the sky to find out
> whether the procedure works.

## 3. What we're doing

1. Make "unit tests have no network" a rule the suite enforces.
2. Record and replay model and tool calls, and prove a replay is byte-identical.
3. Write one contract that every MCP server must pass, and run it against real servers.
4. Break the gateway, the model, a tool and the worker on purpose, and check the platform resumes
   and falls back correctly.
5. Fix whatever the new tests find.

## 4. Why we're doing this

Tests that call real services are slow, flaky, cost money and leak data. Tests that use only
fakes can drift from reality. Cassettes and contracts bridge the two. Outages are when an agent
platform is most likely to do damage: repeating a payment, hammering a struggling gateway, losing
work. Chaos tests check those paths before an outage does.

> *Watch out:* a test suite that "passes" because it skipped the hard cases is worse than none.
> This step's tests found five real defects on their first run.

## 5. Key ideas

- **Scripted fake model.** `ScriptedModelClient` returns a planned sequence of answers and tool
  calls, so a test controls exactly what the "model" does.
- **Contract test.** A shared test class, say "every checkpointer must…", run against every
  implementation. There are 30, plus the new MCP contract.
- **Cassette.** A JSON file of the calls a run made and what came back.
  - Replays answer from it.
  - A request it doesn't hold fails loudly, naming where it differs.
  - Only the edges (model, tools) are recorded. Policy, guardrails and budgets run live.
- **Byte-identical.** The replayed run's calls, written out canonically, are the same bytes as
  the recording.
- **Circuit breaker.** After repeated failures a dependency is skipped for a while, so one outage
  doesn't stall every run. After a cool-down, one trial call is let through.
- **Fallback.** When a model fails, the gateway (LiteLLM) answers with another one.
- **Retry from checkpoints.** A failed run continues where it stopped; completed steps aren't
  done again.

## 6. How to do it

```bash
just test                 # unit + contract tests, no network (≈ 1 minute here)
just test-chaos           # timeouts, outages, retry, slow tools, killed worker, restart
just test-integration     # end to end on the stand-in, incl. cassette recording and fallback
just record-cassettes     # re-record after an intended behaviour change, then review the diff
```

To continue a run that failed because a dependency was down:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8091/v1/runs/<run_id>/retry
```

New MCP server? Add a contract subclass with one example per tool:

```python
class TestMyServer(McpToolContract):
    server = McpServer(name="crm", transport="stdio", command=..., side_effects={...})
    examples = {"find_customer": {"email": "a@example.com"}}
```

## 7. Benefits

- `just test` is fast, offline and deterministic. A test that reaches the internet fails at once.
- Real model behaviour can be tested in CI without a model, a key or the network.
- Every MCP server is held to the same bar before agents rely on it.
- Outage behaviour (retry, circuit, fallback, resume without repeating writes) is tested, not
  assumed.

## 8. Drawbacks and risks

- **Re-recording.** Cassettes must be re-recorded when behaviour changes on purpose, and reviewed
  like code.
- **Retry scope.** Retry re-runs the whole failed node. Writes inside agent nodes rely on
  idempotency keys and approvals.
- **No real network faults.** In-process chaos doesn't test faults in the network itself, such as
  partitions or DNS. Those belong to Step 37's cluster tests.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Interface-level cassettes | VCR.py at the HTTP layer | Testing an HTTP client itself |
| In-repo network guard | pytest-socket | You want its finer per-test allow lists |
| In-process chaos | Toxiproxy, Chaos Mesh | Real clusters, network partitions (Step 37) |
| Gateway fallbacks | Platform-side fallback lists | No gateway in front of the models |

## 10. Which approach fits which situation

- **A new component:** a contract test, plus unit tests with the scripted model.
- **A new agent or prompt:** record a cassette against the stand-in, and replay it in CI.
- **A new MCP server:** an `McpToolContract` subclass.
- **"What happens when X is down?":** a chaos test faking X's transport or clock.
- **Before a release:** `just test-integration` on the full stand-in.

## 11. Common mistakes

- Re-recording a cassette to "make the test pass" without reading the diff.
- Marking a test `integration` just to get past the network guard. If it needs no real service,
  fake the call instead.
- Retrying a run whose failure was a policy denial. Retry is for dependency failures; the policy
  will say no again.
- Faking the circuit breaker or gateway client in chaos tests, which tests the fake rather than
  the platform.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| `just test` runs in under 5 minutes with no network | ✅ | 913 passed, 21 skipped in 64.7 s locally with the guard on. `tests/test_no_network.py` proves sockets, DNS lookups and httpx are refused, including through a loopback proxy, while loopback works. CI's check job, with lint, types, policy and config, took about 2 min 20 s (run 59) |
| Replayed runs are byte-identical | ✅ | `test_a_replayed_run_is_byte_identical_to_its_recording`: a run-API run with parallel tool calls, replayed twice with no model or tools, gives identical transcripts and output. The committed cassette replays offline, and re-recording reproduced it byte for byte. `test_a_run_recorded_against_the_gateway_replays_without_it`: recorded over HTTP against the stand-in LiteLLM, replayed with every socket refused, identical |
| Chaos tests show correct resume and fallbacks | ✅ | `test_chaos.py`: model timeout retried; gateway outage fails fast, opens the circuit (no further gateway calls), and `retry` resumes from checkpoints without repeating the completed tool step; slow tool cut at its limit and the agent finishes; 5 timeouts open the tool circuit; the stand-in LiteLLM fallback serves every call to a failing model. Killed worker: `test_killed_worker_resumes_without_repeating_the_write` (SIGKILL, both adapters) and `test_a_running_run_resumes_from_its_last_checkpoint_after_a_restart` |
| Contract tests; MCP tool contracts | ✅ | 30 contract suites; `test_mcp_contracts.py`: 3 servers × 6 checks offline, plus the filesystem server under `-m integration` |
| ADR | ✅ | ADR 0044 |

## 13. Recap

The pyramid is in place:
- **Bottom:** offline unit and contract tests, guaranteed network-free and running in about a
  minute.
- **Middle:** cassettes that replay real runs byte for byte, and one contract every MCP server
  must pass.
- **Top:** end-to-end tests on the stand-in, and chaos tests that break the gateway, the model,
  tools and workers.

On their first run the new tests found five defects, all fixed:
- no retries on streamed calls;
- no way to continue a failed run;
- unallowlisted MCP tools reachable by direct call;
- a LiteLLM start-up crash;
- an order-dependent alert test.
