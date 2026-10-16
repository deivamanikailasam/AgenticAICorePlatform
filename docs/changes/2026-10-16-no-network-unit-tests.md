# `just test` runs with no network

**Step:** 33 · **Commit:** test: unit tests run with no network · **Date:** 2026-10-16

## What changed
The root `conftest.py` has an autouse guard for every test not marked `integration`, `mac`, `sandbox` or `microvm`:
- It refuses socket connections and name lookups that leave the machine. Loopback and Unix sockets still work, for the in-process stand-ins and fakes.
- It clears the proxy variables, because a proxy listening on loopback would otherwise be a way out.

`tests/test_no_network.py` proves the guard holds for a raw socket, a DNS lookup and an httpx client, that loopback still works, and that integration tests are not guarded.

## Why
Step 33's "Done when": "`just test` runs in under 5 minutes with no network". Without a guard, "no network" is only a hope: one test quietly calling a real API makes the suite slow, flaky and dependent on the internet.

## How to check it
`just test`: 887 passed, 21 skipped in 63 s locally with the guard on, so no unit test needed the network. CI's check job runs the same suite.

## Risks and follow-ups
A new test that genuinely needs a service must say so with a marker (`integration` for the stand-in). Otherwise it fails with "unit tests have no network".
