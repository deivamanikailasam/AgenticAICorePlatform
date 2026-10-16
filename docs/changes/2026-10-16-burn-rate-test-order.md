# Fix a flaky promtool test: the burn-rate alert's value depended on rule-group order

**Step:** 33 · **Commit:** test(observability): make the burn-rate alert test order-independent · **Date:** 2026-10-16

## What changed
The `AgentRunFailureBudgetBurn` test evaluates at 100 minutes instead of 80, with the input series extended so failures hold steady across both burn-rate windows. The expected summary is now "33.33% failing", exactly 30 failed out of 90 runs a minute.

## Why
`just obs-test` failed about one run in three, locally and inside the commit helper: the summary read "32.86%" where "32.96%" was expected.

At 80 minutes, the 1-hour window started exactly where the failures began. The alert's value comes from a recording rule in another group, so it depended on which group promtool evaluated first at that instant. A test whose result depends on evaluation order is a flaky test, and Step 33 wants deterministic tests. Here, all four inputs (both windows, both statuses) are constant at the evaluation time.

## How to check it
`just obs-test` passed 12 times in a row after the change. Before it, it failed in 1 of 3 local runs, and the commit helper failed 3 times on it.

## Risks and follow-ups
None. The no-alert check at 15 minutes and the paging threshold are unchanged.
