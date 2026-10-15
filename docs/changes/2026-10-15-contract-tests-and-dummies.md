# Prove every socket works with two test plugs each

**Step:** 02 · **Commit:** test(contracts): add shared contract suite and two dummies per interface · **Date:** 2026-10-15

## What changed
`agent_contracts.testing` holds one contract test class per interface: the behaviour every
implementation must show. Examples: a tool executor rejects invalid arguments before running
the tool and returns the first result for a repeated idempotency key; a checkpointer never
returns another tenant's state; a policy decision point never plainly allows an irreversible
action; a budget controller trips the named limit and rolls a child's spending up to its
parent; an audit sink detects a tampered record.

Two small *dummy* implementations of each of the 23 interfaces (46 in all) are registered and
must pass those contracts. They double as fast fakes for unit tests, for example the scripted
model client and the sequential runtime adapter.

## Why
A contract test is the socket tester an electrician plugs in before signing off: whichever
brand of socket is installed, it must pass the same test. Two different dummies per socket show
the contract describes behaviour, not one particular implementation.

## How to check it
`just components` ends with `46 components across 23 of 23 slots`.
`uv run pytest libs/agent-contracts` reports every contract passing.

## Risks and follow-ups
Dummies are not production components; each real implementation (Steps 5–35) must pass the
same contract before it is registered.
