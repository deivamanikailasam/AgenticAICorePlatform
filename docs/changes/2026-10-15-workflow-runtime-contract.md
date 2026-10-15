# One exam every runtime engine must pass

**Step:** 11 · **Commit:** feat(contracts): add the workflow runtime contract · **Date:** 2026-10-15

## What changed
`agent_contracts.testing.WorkflowRuntimeContract` extends the basic runtime contract with the
behaviour a production engine needs, each as a small plan with a known answer:
- two branches run **in parallel** and a **join** waits for both (`A+B+C`, faster than running
  them one after the other);
- **routing by outcome** (`big` or `small`);
- a failure **handled by an `on_error` edge**, and an unhandled failure that **fails the run**;
- **`max_steps`** stops an endless loop with status `stopped`;
- **cancel** stops a node that is still running, and the waiting caller gets a `cancelled`
  result;
- a **rejected approval** takes the rejected branch;
- the **event stream** reports every node with its outcome;
- the same plan with the same inputs takes the **same path**.

The test functions are module-level, so specs can name them by dotted path.

## Why
Like a driving test that every car must pass on the same course: if two engines pass it, a spec
behaves the same on either, and swapping engines is safe.

## How to check it
Used by `libs/agent-runtime/tests/test_langgraph_adapter.py` and
`test_agent_framework_adapter.py` (next commits).

## Risks and follow-ups
Timing assertions use generous margins (parallel branches must finish in under 1.8 times one
branch) to stay stable on slow CI machines.
