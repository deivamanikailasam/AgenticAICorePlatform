# Call-time tool credentials, audited and never seen

**Step:** 29 · **Commit:** feat(agent-tools): short-lived credentials issued per tool call · **Date:** 2026-10-15

## What changed
- New spec field `tools[].credentials`: an OpenBao role such as `agent-db/creds/tool-reader`. It is validated as `<mount>/creds/<role>`, so a DSN or a secret value is refused. The compiler carries it into the plan, and `grants_for` into `ToolGrant.credentials`.
- `AuthorisingToolExecutor` gains an optional `broker`. After policy and the rate limit, for a granted source it:
  1. issues a credential for this one call;
  2. hands it to the tool through `current_credential` (`agent_tools.credentials`);
  3. revokes it when the call ends;
  4. withholds any result that contains the credential's values;
  5. audits the lease (id, source, issued to, expiry, revoked) next to the user and agent.

  If the call has no broker, or the credential is refused, the call is stopped (fail closed). If the broker is down, the call is marked unavailable.
- New tool, `local://platform/sql_read`:
  - The model picks a query by name and gives its parameters. The queries come from the spec, so the model never writes SQL.
  - Only a single SELECT/WITH statement is accepted.
  - Each call logs in with that call's credential, in a read-only transaction with a statement timeout.
- `Assembly(broker=...)` passes the broker to every run's executor.

## Why
This covers the Step 29 "Done when" checks:
- a tool call's audit record names the user, the agent and the credential lease;
- credentials never enter model context;
- no secret appears in any trace or prompt.

It also covers OWASP ASI03.

## How to check it
- `uv run pytest libs/agent-tools/tests/test_call_credentials.py`. 8 tests:
  - a fresh lease per call, revoked after;
  - fail closed without a broker;
  - refused or unavailable credentials stop the call;
  - an echoed credential is withheld;
  - the spec refuses secrets.
- `just test-integration services/agent-api/tests/test_credentials_in_runs.py`. Against OpenBao and Postgres:
  - A run looks a supplier up with a real 5-minute database user.
  - The audit record names alice, the agent and the lease. After the call, the lease is revoked and the database user is dropped.
  - The user name and password appear in no prompt, run event, run record or audit record.
  - A traced run's spans are free of them too.

## Risks and follow-ups
- The run API emits spans only from Step 30. Until then, the trace check runs on the traced runner (`just run`) with the same executor and broker. Step 30 must add the same check to run-API spans.
- In this commit, the broker is passed in by tests. Services get a broker with per-agent identities in the next commit.
- `sql_read` registers under the name `sql_read`, so one spec can hold one such tool.
