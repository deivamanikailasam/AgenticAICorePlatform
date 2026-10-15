# "Is this person related to this invoice?": OpenFGA relationship checks

**Step:** 10 · **Commit:** feat(agent-tools): add OpenFGA relationship checks and the composite policy decision point · **Date:** 2026-10-15

## What changed
- `OpenFgaRelationshipChecker` asks OpenFGA "is `user:alice` a `payer` of
  `invoice:acme/inv-1`?". `policy_decision_point.composite` asks the main policy engine (OPA or
  Cerbos) first and then, for tools listed in `policies/openfga/relations.yaml`, checks the
  relationship. A missing relationship, a missing invoice id or an OpenFGA failure means deny; a
  relationship never turns a deny into an allow, and payments still need approval.
- Object ids include the tenant (`invoice:acme/inv-1`), so the same invoice number in another
  tenant is a different object.
- `policies/openfga/model.json` is the authorisation model (users, groups with members,
  invoices with an owning team, viewers and payers). `just provision-openfga` creates the store
  and writes the model only when it changed.
- OpenFGA runs in Project A's compose under the `openfga` profile with a pre-shared key on every
  call. The gateway uses it with `MCP_GATEWAY_PDP=opa+openfga` or `cerbos+openfga`; unknown
  values stop the gateway at start-up.

## Why
Rules like "finance staff may pay invoices" are not enough when only the team that owns an
invoice should pay it. That is a relationship, like a club where your membership card opens only
your own locker, not every locker of the same size.

## How to check it
`uv run pytest libs/agent-tools/tests/test_relationships.py` (8 offline cases). With the
stand-in, `just up-alternatives` and `just provision-openfga`, the integration test shows alice
(member of the owning team, payer) allowed to view and needing approval to pay, bob denied, and
the same invoice id in tenant globex denied. A second `just provision-openfga` prints
`model: up to date`.

## Risks and follow-ups
The memory datastore loses relationships on restart; production must use OpenFGA's Postgres
datastore and the owning applications must write relationships. Each checked call adds one
network round trip.
