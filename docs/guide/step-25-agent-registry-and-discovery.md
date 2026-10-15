# Step 25 · Agent registry and discovery

## 1. At a glance

| | |
|---|---|
| **Goal** | One list of every agent, its owner and its state; only approved agents run in staging and production |
| **Time** | About a day |
| **You end up with** | `libs/agent-registry` (Postgres default, Backstage catalogue alternative); `just agents`; lifecycle draft → approved → deprecated → retired; a gate in the run API and the worker |
| **New words** | registry, lifecycle state, approval expiry, spec hash, Backstage catalogue |

## 2. The big idea

A company should always be able to answer: which agents do we have, who owns each one, what can
it touch, and is it allowed to run? The registry is that answer. Every agent version starts as
a **draft**. Someone approves it before it can run in staging or production, and it is later
deprecated and retired.

> *Analogy:* the staff directory plus security badges. A new starter is in the directory from day
> one, but the badge only opens the production floor after a manager signs it off, and badges
> expire.

## 3. What we're doing

1. Define the registry as a switchable part, with lifecycle rules that every implementation
   must follow.
2. Store it in Postgres by default, with Backstage catalogue files as the alternative and
   SQLite for local use.
3. Register every spec automatically as a draft, with a fingerprint of its content.
4. Add `just agents` to list agents and change their status.
5. Enforce "approved only" in staging and prod, when a run starts and before a team delegates.

## 4. Why we're doing this

Without a registry:
- agents appear without anyone owning them;
- an edited agent can slip into production without review;
- a team can hand work to an experimental agent.

OWASP's ASI10 (rogue agents) asks for exactly this: a registry with lifecycle, owner and
expiry.

> *Watch out:* "approved" means one specific version with one specific content. Changing the
> spec without changing its version is refused. Otherwise the approval would cover something
> nobody reviewed.

## 5. Key ideas

- **Record.** For each agent version the registry keeps:
  - name, version, owner, risk tier;
  - autonomy, pattern, tools, team members;
  - eval status and a summary card;
  - status, who changed it, why and when;
  - an optional approval expiry.
- **Lifecycle.**
  - draft → approved or retired;
  - approved → deprecated or retired;
  - deprecated → approved or retired;
  - retired is final.

  Every change records who made it, and why.
- **Spec hash.** A fingerprint of the spec's content. If the same version is registered with
  different content, it is refused.
- **The gate.** In `dev` anything runs. In `staging` and `prod`:
  - starting an agent that isn't approved returns 403;
  - a team cannot delegate to one: the worker is refused before it is even built, and the
    refusal is audited.
- **Expiry.** Approvals can lapse, which forces a periodic re-review.

## 6. How to do it

```bash
just agents                                   # sync agents/ and list all agents with status
just agents --approve research-report-writer 0.1.0 --by carol --reason "evals pass" --expires 2027-01-31
just agents --deprecate hello-workflow 0.1.0 --by carol --reason "replaced by 0.2.0"
just agents --backstage catalog/agents        # use the Backstage catalogue instead
```

Choose the registry and environment:

```bash
AGENT_ENV=staging                 # dev | staging | prod
AGENT_REGISTRY=postgres           # postgres (with AGENT_DB_DSN) | backstage | sqlite | memory
just migrate                      # creates agent_registry.agents and status_history
```

## 7. Benefits

- One place to see every agent, its owner and its state.
- Nothing unreviewed runs in staging or production, including workers inside teams.
- Changes to a reviewed version are caught automatically.
- Expiring approvals keep reviews current.
- A full history of who approved what, and why.

## 8. Drawbacks and risks

- Every new version needs an approval before it reaches staging. That is deliberate friction.
- Deprecating an agent does not stop runs already in progress. The kill switch (Step 38) does
  that.
- Who may approve (for example, not the owner) is not enforced yet; that comes with Step 28.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Postgres registry | Backstage catalogue in Git | Your organisation already discovers services in Backstage and approves through pull requests |
| Separate approval step | Status inside `agent.yaml` | Never for production: authors would approve their own agents |
| Gate in the API and worker | OPA rule with registry data | Registry data is already bundled into OPA (useful for the kill-switch drill) |
| SQLite locally | In-memory | Tests, where nothing should persist |

## 10. Which approach fits which situation

- **A developer trying a new agent:** `dev`, where drafts run.
- **A release to staging:** run its evals, then `just agents --approve ... --expires`.
- **An agent being replaced:** approve the new version, then deprecate the old one; retire it
  once nothing uses it.
- **A Backstage shop:** `AGENT_REGISTRY=backstage`, with approvals as reviewed pull requests to
  `catalog/agents`.

## 11. Common mistakes

- Editing a spec and expecting the old approval to cover it. Bump the version.
- Approving without an expiry for high-risk agents.
- Using a local SQLite registry in staging (refused at start-up).
- Approving the team agent but forgetting its workers. Each worker is an agent with its own
  approval.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| `just agents` lists all agents with status | ✅ | Lists all 20 agent versions in `agents/` (including team workers) with version, status, owner, risk, autonomy, pattern, eval status and tool count; `test_cli.py` also approves through it |
| A supervisor cannot delegate to a draft agent in staging or prod | ✅ | `test_a_supervisor_cannot_delegate_to_a_draft_agent_in_staging`: researcher and analyst run; the draft writer is refused, never assembled, and audited (`registry.refused`); the run ends `stopped`. Also enforced in the Temporal worker |
| Catalogue: version, owner, risk tier, tools, autonomy, eval status, card | ✅ | `AgentRecord` and `card_of`; sync test checks owner and tools |
| Lifecycle draft → approved → deprecated → retired | ✅ | `AgentRegistryContract`: legal and illegal moves, history, retired hidden; it passes for memory, SQLite, Backstage and Postgres (stand-in, 6 passed) |
| Only approved agents run in staging and prod; owner and expiry (ASI10) | ✅ | A draft agent in prod returns 403; the gate refuses not-registered, draft, expired and changed specs (4 refusals audited); staging refuses a local registry |
| Alternative: external catalogue | ✅ | `agent_registry.backstage`: Component entities with lifecycle; reload; Location |
| ADR | ✅ | ADR 0036 |

## 13. Recap

The registry lists every agent version with its owner, risks and state. Versions are fixed once
registered, approvals can expire, and every change is recorded. In staging and production, only
approved agents start, and teams cannot hand work to anything else. This holds whether the
team runs in the API or in the Temporal worker.
