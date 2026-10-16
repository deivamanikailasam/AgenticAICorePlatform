# Database connection strings as OpenBao references

## What changed
- `agent_tools.secrets.resolve_env_references()`: at start-up, `AGENT_DB_DSN` and
  `AGENT_AUDIT_DSN` may hold a `${secret:path#key}` reference instead of a connection string; it
  is resolved through the process's resolver (OpenBao in staging and prod) before anything reads
  it. Values that are not references are left as they are.
- agent-api, agent-worker, mcp-gateway and a2a-gateway call it first thing in `main()`.

## Why
Step 38 deploys to Kubernetes. A connection string carries a password; with a reference, the
password lives only in OpenBao, not in a manifest or a Kubernetes Secret.

## How to check it
`uv run pytest libs/agent-tools/tests/test_openbao.py -m "not integration"`
(`test_connection_strings_can_be_openbao_references`).

## Risks and follow-ups
- The resolved value is in the process environment, as before in dev. Moving the services to
  dynamic database credentials from OpenBao's database engine (as tools already do) is the
  stronger option for a later change.
