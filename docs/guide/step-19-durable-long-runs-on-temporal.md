# Step 19 · Durable long runs on Temporal

## 1. At a glance

| | |
|---|---|
| **Goal** | Runs that wait days for a person, survive any restart, and never repeat a finished action |
| **Time** | About two days |
| **You end up with** | `durability: temporal`, a Temporal dev server, the `agent-worker` service, approvals as signals with a 7-day timer, large values in S3 by reference, `durable_executor.temporal` (Restate and DBOS stubs) |
| **New words** | workflow, activity, signal, durable timer, heartbeat |

## 2. The big idea

Step 18 made each machine keep a diary of a run, so whoever takes over can pick it up. Step 19
hands the diary to a separate server, Temporal, whose only job is to remember. The agent's
workers just do the work they are given. If every worker stops, or the server itself restarts,
the run is still there and continues when a worker comes back. Waits ("approve within 7 days")
are counted by the server, not by a process that might die.

> *Analogy:* a project manager with a ledger. Workers come and go, but the manager writes down
> every task handed out and every result returned, and keeps a calendar of deadlines. A new
> worker is simply handed the next task. No one has to remember anything.

## 3. What we're doing

1. Run each agent's compiled plan inside one generic Temporal workflow.
2. Run every node (model, tool, function, sub-agent) as an activity with retries and heartbeats.
3. Turn approvals into signals with a durable timer.
4. Pass large values by reference through S3.
5. Put the worker in its own service and start Temporal with `just up`.
6. Offer the same engine as a `durable_executor`, with Restate and DBOS as documented stubs.

## 4. Why we're doing this

Real approvals take hours or days, while deploys and crashes happen every day. Checkpoints
(Step 18) let a run continue after a crash, but nothing wakes it at a deadline, and someone has
to notice it stopped. Temporal does both: it reschedules work on a live worker and fires timers
on time.

> *Watch out:* durable does not mean "never repeats". A step that was running when its worker
> died runs again. Tool idempotency keys (Step 12) are what stop a payment from being made twice.

## 5. Key ideas

- **Workflow.** The run's decision-maker: given what has finished, what runs next? It never
  calls a model or tool itself, so the server can rebuild its state at any time by replaying
  the history.
- **Activity.** One node's real work, run by a worker. Retries: 5 attempts, backoff from 1 s to
  30 s. Limit: 10 minutes per attempt.
- **Heartbeat.** A running node reports in at start and every 5 seconds. With no heartbeat for
  30 seconds, the server assumes the worker died and retries the node elsewhere.
- **Signal and durable timer.** An approval node waits for an `approve` signal. The 7-day
  deadline is a timer on the server. When it fires, the outcome is `expired` and the run takes
  its error path (with compensation).
- **By reference.** Values over 256 KiB are written to S3, and only `{"$ref": "s3://..."}` goes
  into the history. That keeps the history small and keeps customer data out of it.
- **One run, one workflow id.** `agent-run-<run id>`: starting the same run twice attaches to
  the first.

## 6. How to do it

Start everything; Temporal comes up with the platform:

```bash
just up            # gateway, API, temporal (127.0.0.1:7233, UI on :8233), agent-worker
docker logs agent-platform-agent-worker-1
```

Make an agent durable on Temporal in its `agent.yaml`:

```yaml
runtime: { adapter: langgraph, durability: temporal }
```

The worker serves every such agent at start-up. From Python, a client starts a run, and later
approves it from anywhere:

```python
rt = TemporalRuntime()  # TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE
handle = await rt.build(compile_spec(spec))
paused = await rt.invoke(handle, {"invoice": "INV-7"}, ctx)  # returns when it waits
done = await rt.resume(handle, ctx.run_id, {"approved": True, "by": "jo"}, ctx)
```

Watch runs in the Temporal UI at `http://127.0.0.1:8233`.

## 7. Benefits

- Runs can wait days, and the waiting costs nothing: no process holds them.
- Worker and server restarts, deploys and crashes do not lose runs.
- Every decision is recorded, so the history shows exactly what ran, and how often.
- Workers scale on their own, separately from the API.

## 8. Drawbacks and risks

- One more server to run and back up. In production that means a Temporal cluster with Postgres,
  or Temporal Cloud.
- Workflow code must be deterministic. Changing a plan for runs already in flight needs a new
  plan version.
- The worker has no user token, so on-behalf-of tools wait for delegated credentials
  (Step 29).
- The run API does not yet send `durability: temporal` agents to the worker or stream their
  per-node events.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Temporal | Step 18 checkpoints | Runs of minutes with no long waits: less to operate |
| Temporal | Restate (stub) | Request/response services that need durable steps and a light server |
| Temporal | DBOS (stub) | Only Postgres is allowed and no extra server can be run |
| Values by reference | Inline values | Small values (under 256 KiB), which is most of them |

## 10. Which approach fits which situation

- **Invoice approval that can take a week:** Temporal, with the 7-day timer and expiry on the
  error path.
- **Chat assistant answering in seconds:** checkpoints (Step 18); Temporal adds nothing.
- **Nightly batch of thousands of documents:** Temporal, so workers can be added or lost
  freely.
- **Team that can only run Postgres:** Step 18 checkpoints now; DBOS once it is implemented.

## 11. Common mistakes

- Calling a model or tool, reading the clock or using random numbers inside workflow code.
  These belong in activities.
- Putting large documents into the workflow history instead of S3.
- Treating a retried activity as "safe" without an idempotency key on the tool.
- Changing an agent's plan without bumping its version while runs are still waiting.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| A run waits 24 hours (simulated) for an approval, then finishes | ✅ in CI | the time-skipping test server advances 24 hours; the run is still waiting, then approved and paid. The server cannot be downloaded in this container, so the test runs in CI (`AGENT_REQUIRE_TEMPORAL_TIMESKIP=1`) |
| Survives a worker and server restart | ✅ | the run pauses for approval; the worker stops; `docker compose restart temporal`; a new worker reads the state, takes the approval and the run finishes |
| History shows no duplicate side effects | ✅ | the workflow history scheduled `record` and `pay` exactly once each, and each function ran once |
| Model and tool calls as activities with retries and heartbeats | ✅ | every node is the `run_node` activity with a retry policy; the activity heartbeats (unit test) |
| Durable timers and signals | ✅ | an approval is a signal; an unanswered one expires after the 7-day timer (time-skipping test, in CI) |
| Large payloads in S3 by reference | ✅ | values over the limit are stored and passed as `$ref`, then resolved for the next node |
| Restate or DBOS | ✅ stubs | documented stubs (ADR 0030) with a test |
| ADR | ✅ | ADR 0030 |

## 13. Recap

Step 19 moved long runs onto Temporal. A generic workflow walks each agent's plan and hands
every node to a worker as an activity. Retries and heartbeats handle crashed workers. Approvals
are signals with a deadline kept by the server, so a run can wait a week and still finish after
any restart, without repeating what it already did.
