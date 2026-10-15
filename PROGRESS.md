# Progress

Resume point for any new session. Updated after every commit.

**Current step:** 1 · Repo, workspace and link to Project 0 (in progress)

## Environment note (read first)

This build runs in a **cloud container**, not on the Mac where Project 0 lives. Consequences:

- Project 0 services (LiteLLM, Postgres, Valkey, Keycloak, OPA, OTel Collector, …) are **not
  reachable** from here. Any "Done when" item that needs a live Project 0 is marked
  **VERIFY-ON-MAC** below and is *not* ticked until someone runs it on the Mac.
- Commits are authored as `deivamanikailasam` but are **unsigned** (the SSH signing key lives on the
  Mac). The owner accepted this on 2026-10-15.
- Project 0's README, ADRs and justfile were not readable from here; conventions are taken from
  the spec. Cross-check against `~/dev/rag-platform` on the Mac.

## Done

- Spec exported to `docs/spec/` (Claude Doc revision 21).

## Next

- Step 1: workspace, hygiene files, justfile, pre-commit, SOPS, ADRs 0001–0010, compose,
  provisioning scripts, health script, CI.

## Open issues

| # | Issue | Owner |
|---|---|---|
| 1 | Project 0 unreachable from the build container: live checks deferred (VERIFY-ON-MAC) | owner |
| 2 | Commits unsigned; re-sign or accept when merging | owner |
