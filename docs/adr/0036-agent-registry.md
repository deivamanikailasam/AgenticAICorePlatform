# ADR 0036: Agent registry in Postgres with lifecycle states; only approved agents run in staging and prod

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 25
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 25 asks for a catalogue of every agent with its version, owner, risk tier, tools, autonomy,
eval status and card, and lifecycle states (draft → approved → deprecated → retired).
Supervisors may delegate only to approved agents. The default is a Postgres registry; the
alternative is an external catalogue.

Done when: `just agents` lists all agents with status, and a supervisor cannot delegate to a
draft agent in staging or prod. OWASP ASI10 (rogue agents) also asks for registry lifecycle
with owner and expiry. No new third-party packages were needed.

## Decision
1. **A new switchable part, `agent_registry`.**
   - Interface: register, get (newest version), list, set status, set eval status, history.
   - Its contract fixes the lifecycle rules for every implementation. Allowed moves are
     draft → approved | retired, approved → deprecated | retired, and deprecated → approved |
     retired; retired is final.
   - Every move needs a `by` and is kept in the history.
2. **Versions are immutable once registered.** The record carries a SHA-256 of the canonical
   spec. Registering changed content under an existing version is refused, and the gate refuses
   a running spec whose content differs from the registered one. A reviewed version cannot be
   edited quietly; changes need a new version.
3. **Implementations:**
   - Postgres (default): schema `agent_registry`, row-locked status changes, history in the
     same transaction, a namespace per environment;
   - Backstage catalogue entities in Git (the "external catalogue" alternative);
   - in-memory and SQLite for tests and local `just agents`.
4. **The gate (`ApprovedOnly`):**
   - In `dev` everything runs.
   - In `staging` and `prod` the exact version must be registered with the same content,
     approved and not past its expiry.
   - Refusals are audited (`registry.refused`).
   - It is enforced at run creation (403) and before every delegation in team runtimes, before
     the worker is built and ahead of the `agent.delegate` policy. This holds in both the API
     and the Temporal worker.
   - Staging and prod refuse local registries.
5. **Sync, not hand entry.** Every spec in `agents/` (including team workers) is registered as
   draft when first seen (`just agents`, API start-up). Approving, deprecating and retiring are
   explicit actions with a name and reason.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Status in each `agent.yaml` | The spec author would approve their own agent; status changes would need a code change and redeploy |
| Backstage as the default | Good discovery UI, but lifecycle is free text and needs Git round-trips; kept as the alternative for organisations already on Backstage |
| Enforce in OPA only | OPA would need registry data pushed into its bundle; the gate reads the registry directly and audits. A rego rule can be added when registry data is bundled (Step 38 kill-switch drills) |
| Gate only at run creation | Teams would still delegate to draft workers, which is exactly the Done-when case |

## Consequences
- Every new spec version must be approved before it runs in staging or prod. This is
  deliberate friction, and the expiry date forces a periodic re-review.
- Runs already in progress continue if their agent is deprecated. Stopping a version
  immediately is the kill switch (Step 38).
- Who may approve (for example, not the owner) is not enforced yet. That belongs to the
  approval workflow (Step 28).

## Verification
- Contract `AgentRegistryContract` (5 tests) runs against memory, SQLite, Backstage and
  Postgres. Postgres ran with `-m integration` on the stand-in: 6 passed.
- `libs/agent-registry/tests`:
  - sync registers all 20 specs as draft once;
  - an edited spec without a new version is refused;
  - gate cases: dev allows; staging refuses not-registered, draft, expired and changed specs,
    and allows approved (4 refusals audited);
  - `just agents` lists and approves;
  - staging refuses a local registry;
  - Backstage entity shape and reload.
- `services/agent-api/tests/test_registry_gate.py`:
  - in staging, `research-report` with a draft writer delegates to researcher and analyst; the
    writer is refused, never assembled, and audited; the run ends `stopped`;
  - a draft agent in prod returns 403;
  - drafts run in dev.
