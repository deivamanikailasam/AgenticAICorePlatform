# OpenBao provisioning recovers when OpenBao lost its database connection

**Step:** 29 · **Commit:** fix(provision): recover OpenBao's database connection after OpenBao loses its storage · **Date:** 2026-10-15

## What changed
`just provision-openbao` can now find that the manager role `agent_bao` exists in Postgres while OpenBao has no `agent-db/config/agent` connection. In that case it resets the role's password to a new random value. It then recreates the connection and lets OpenBao rotate the password again. Before this fix, provisioning stopped and asked for the role to be dropped by hand.

## Why
This happened for real in the stand-in. Docker restarted, and dev-mode OpenBao (in memory) came back empty while Postgres kept its volume. The same can happen on Project 0 after an OpenBao restore that doesn't match Postgres. Resetting the password of a role that only OpenBao uses is safe: provisioning already holds the Postgres admin login, and nobody else knows the old password.

## How to check it
`just test-integration tests/scripts/test_provision.py`. The new test deletes the connection and provisions again. The output says "password reset", OpenBao rotates the password, and a credential is issued with a lease of at most 15 minutes.

## Risks and follow-ups
Database users issued before the reset still expire on their `VALID UNTIL` time. OpenBao no longer tracks their leases, though, so they can't be revoked early.
