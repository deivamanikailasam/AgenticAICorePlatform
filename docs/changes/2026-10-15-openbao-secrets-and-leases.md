# OpenBao: platform secrets, dynamic tool credentials, provisioning

**Step:** 29 · **Commit:** feat(agent-tools): OpenBao secret resolver, credential broker and provisioning · **Date:** 2026-10-15

## What changed
- `agent_tools.openbao`:
  - `OpenBaoClient`: token, AppRole or JWT login, and logging in again shortly before the token expires.
  - `OpenBaoSecretResolver`: KV v2 lookup for `${secret:path#key}`.
  - `OpenBaoCredentialBroker`: implements `credential_broker.openbao`. It issues credentials from `<mount>/creds/<role>` and revokes them through `sys/leases/revoke/<lease>`. A lease that is missing or longer than 15 minutes is refused and revoked. With `agent_auth`, the broker logs in once per agent as that agent.
  - `credential_broker.vault`: the same HTTP API under Vault's name.
- `just provision-openbao`, now part of `just provision`, configured by `infra/provision/openbao.yaml`. It sets up:
  - a KV v2 mount, `agent-platform`;
  - a database secrets engine, `agent-db`, on the `agent` database:
    - The manager role `agent_bao` is created with a random password, and OpenBao rotates it straight away.
    - The role `tool-reader` creates a user that lives 5 minutes (15 at most). The user belongs only to `agent_tool_reader`, which can read the `tool_data` schema.
    - Revocation ends the user's open sessions and drops the user.
  - the runtime's AppRole, whose policy may only read the KV mount;
  - an import of `.tmp/litellm-keys.env` into `agent-platform/litellm/<role>`. The file is deleted once every key reads back. This closes the manual step in open issue 4.
- OpenBao 2.7.0 in the stand-in, pinned by digest and bound to 127.0.0.1:8200 (dev mode, CI only).
- `just health` checks OpenBao is unsealed. `P0_OPENBAO_URL` is added to the compose endpoints.

## Why
Step 29 requires that tools get short-lived credentials from OpenBao at call time, never from a prompt or the environment, and that a leaked lease expires within minutes.

## How to check it
- `just standin-up && just provision-openbao`. A second run prints only "exists".
- `just test-integration libs/agent-tools/tests/test_openbao.py tests/scripts/test_provision.py`. Checked against the live stand-in:
  - The contract passes against OpenBao.
  - An issued user can read `tool_data` and nothing else.
  - Revoking ends the open session and blocks a new login.
  - A 3-second lease that nobody revokes expires and can't log in.
  - The runtime AppRole can read KV, but can't write it and can't issue tool credentials.

## Risks and follow-ups
- Postgres details:
  - Ending sessions on revoke relies on Postgres 16+ `createrole_self_grant = 'inherit'` on the manager role.
  - The default revocation statements don't work here, because they revoke grants on tables the manager doesn't own, so the role defines its own.
- VERIFY-ON-MAC:
  - Project 0's OpenBao service name and port.
  - The host name it uses to reach Postgres (`AGENT_BAO_DB_HOST`).
  - Whether its KV and auth mounts are free to use.
- `credential_broker.vault` is verified against OpenBao only.
