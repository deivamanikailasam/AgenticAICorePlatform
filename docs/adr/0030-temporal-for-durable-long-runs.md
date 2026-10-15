# ADR 0030: Temporal for durable long runs; plan interpreted in the workflow; Restate and DBOS as stubs

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 19
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 19 says: `durability: temporal` runs the agent as a Temporal workflow. Model and tool calls
are activities with retry policies. Durable timers and signals handle multi-day waits and
approvals. Long tools send heartbeats. Large payloads go to S3 by reference. Restate or DBOS are
the alternatives.

Done when: a run waits 24 hours (simulated) for an approval, survives a worker and a server
restart, then finishes, and its history shows no duplicate side effects.

Checked 2026-10-15 under the 14-day cooldown (ADR 0022):
- `temporalio` 1.34.0, the newest Python SDK release at least 14 days old.
- The Temporal CLI image `temporalio/temporal:1.9.1`, pinned by digest. Its `server start-dev`
  is the documented local server.
- Project 0 does not run Temporal, so this is a Project A service. That is not a reuse
  conflict.

## Decision
1. **One generic workflow interprets the compiled plan** (`agent-run`). Its decisions are only
   routing (`next_targets`, shared with the other engines), joins, approvals, step limits and
   compensation. This keeps the same plan semantics on every engine, and no per-agent workflow
   code is generated.
2. **Every node runs as one activity** (`run_node`):
   - retries: 5 attempts, backoff from 1 s to 30 s;
   - a 10-minute start-to-close limit and a 30-second heartbeat timeout;
   - a heartbeat on start and then every 5 s.

   Tool nodes keep their idempotency key `<run>:<node>` (Step 12), so a retried write is not
   repeated.
3. **Approvals use a signal behind a durable timer.** The workflow waits on `approve` for 7 days
   by default (configurable). Without a decision the node's outcome is `expired`, which takes the
   error path and the saga compensation.
4. **Large values go by reference.** Values over 256 KiB go to the artefact store (Project 0
   SeaweedFS S3) and travel as `{"$ref": uri}`. That stays under Temporal's 2 MB payload limit
   and keeps customer data out of the workflow history.
5. **Workflow ids are the idempotency key.** `agent-run-<run id>` and `fn-<tenant>-<key>` use
   the id conflict policy USE_EXISTING, so starting the same run twice attaches to the first.
6. **The worker is its own service** (`services/agent-worker`), as the spec's layout says. It
   uses the run API's `Assembly`, so tools are under the same OPA policy and audit as everywhere
   else. `TemporalRuntime` also implements the runtime adapter contract for in-process use and
   tests.
7. **`durability: temporal` selects the Temporal runtime** unless a `runtime_adapter` component
   is pinned.
8. **`durable_executor.temporal`** passes the durable executor contract.
   `durable_executor.restate` and `durable_executor.dbos` are **documented stubs**: they raise
   with a pointer to this ADR, and a test pins that behaviour.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Generate one Temporal workflow class per agent | Versioning and determinism review per agent, and drift from the other engines' semantics |
| Run the whole agent in one activity | One retry unit for the whole run: a crash repeats every call, and there are no per-node timeouts |
| Restate (implement now) | Strong fit for request/response services with durable steps. It needs a Restate server and its own SDK, and it is not in Project 0. It is a stub until a team needs it |
| DBOS (implement now) | Durable steps in Postgres with no extra server, a good fit when only Postgres is allowed. It is a stub until needed. Step 18's checkpoints already cover "Postgres only" for short runs |
| Step 18 checkpoints for multi-day waits | They work, but nothing wakes the run at a deadline and there is no server-side timer or signal |

## Consequences
- Workflow code must stay deterministic. Changing a plan while runs are in flight needs a new
  plan version (the handle includes it).
- The worker has no user token: on-behalf-of tools wait for delegated credentials (Step 29).
- The run API still runs agents in-process. Starting `durability: temporal` agents from the API,
  with per-node AG-UI events streamed back from the worker, is a follow-up.
- The development server keeps its state in SQLite on a volume. Production needs a Temporal
  cluster with Postgres, or Temporal Cloud (Step 37).

## Verification
- `libs/agent-runtime/tests/test_temporal.py`: the activity offloads and resolves references and
  heartbeats; the Restate and DBOS stubs raise as documented.
- `libs/agent-runtime/tests/test_temporal_integration.py`:
  - the durable executor contract;
  - an approval wait that survives a worker stop and a server restart, with each side effect
    scheduled once in the history;
  - a 24-hour wait, and expiry after 7 days, on the time-skipping server (in CI).
- `libs/agent-compiler/tests/test_compiler.py`: runtime selection.
- `services/agent-worker/tests/test_worker.py`: a separate worker executes a client's run.
