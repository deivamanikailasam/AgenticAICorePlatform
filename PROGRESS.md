# Progress

Resume point for any new session. Updated after every commit.

**Current step:** 4 · Pattern compiler and runtime adapter skeleton (next)

## Environment note (read first)

This build runs in a **cloud container**, not on the Mac where Project 0 lives (ADR 0011):

- Project 0 is not reachable. Integration checks run against the **stand-in stack**
  (`tests/stack/`, same upstream images). Items needing the real Project 0 are
  **VERIFY-ON-MAC** and are not ticked.
- Commits are authored as `deivamanikailasam`, **unsigned** (accepted by the owner, 2026-10-15).
- Project 0's README/ADRs/justfile were not readable; conventions come from the spec.
- Work happens on branch `claude/wonderful-heisenberg-kssi9q`; it is merged to `main` at the end
  (owner's instruction). Step tags `step-NN` are created on this branch.

## Status by step

| Step | State | Verified here | VERIFY-ON-MAC |
|---|---|---|---|
| 1 Repo, workspace, link to Project 0 | done | `just check`; health, provisioning (idempotent) and `just up` against the stand-in; CI | health/provision/up against real Project 0; service names, realm, network name; SOPS file with Project 0's age key |
| 2 Contracts and component registry | done | 46 dummies pass 23 contracts; `just components`; catalogue check | — |
| 3 The agent.yaml spec | done | readable errors with lines; `just validate agents/`; schema + reference generated and checked | — |

## Next

- Step 4: `compile(spec) -> ExecutablePlan`, a PatternBuilder per `pattern:`, the
  `RuntimeAdapter` skeleton; hello workflow runs end to end through the stub adapter with a
  trace in Tempo (Tempo is VERIFY-ON-MAC; stand-in has an OTel Collector only).

## Open issues

| # | Issue | Owner |
|---|---|---|
| 1 | Project 0 unreachable from the build container: live checks deferred (VERIFY-ON-MAC) | owner |
| 2 | Commits unsigned; re-sign or accept when merging | owner |
| 3 | `secrets/dev.enc.env` must be created on the Mac with Project 0's age key | owner |
| 4 | LiteLLM key values from provisioning must be moved into OpenBao (automated in Step 29) | Step 29 |
