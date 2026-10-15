# ADR 0043: Governance artefacts generated from specs; CODEOWNERS by risk tier; a CI gate for high-risk changes

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 32
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 32 asks for:
- agent and tool cards (purpose, owner, risk tier, autonomy, tools, data, limitations, eval
  results), generated from specs and registries;
- a system card per reference app;
- lineage from spec to prompts, models, tools and datasets;
- change approval rules in CODEOWNERS by risk tier;
- an AI-use register entry per agent.

Done when:
- cards regenerate in CI and fail the build if stale;
- a high-risk spec change cannot merge without the named approver.

Tool cards existed since Step 6 (`just cards`, checked in `just check`). The repository has one
maintainer. All 20 current specs are low or medium risk.

## Decision
1. **One generator, deterministic inputs.** `libs/agent-governance` builds every artefact from
   the specs, `platform.yaml`, the tool registry, `governance/approvers.yaml` and recorded eval
   runs.
   - It never reads a database or service, so CI regenerates identically.
   - `just cards --check` fails on any stale or orphaned file, and it already runs in CI.
2. **Fields come from enforced configuration.**
   - Budgets become "stops at…".
   - Tool side effects come from the registry or `platform.yaml`, and approvals from the spec.
   - Guardrails show the always-on baseline first.
   - Unknowns are stated as unknown. An MCP server missing from the allowlist is "undeclared",
     not guessed.
   - Eval results are cited only from a recorded run (`evals/results/<agent>.json`, with dataset
     size and model). Otherwise the card says "No recorded eval run".
3. **Reference apps are the top-level agents.** Each gets a system card covering its workers.
   A system's risk tier is the highest of its agents.
4. **Lineage reuses the audit identifiers.** The spec hash is the same digest as the audit
   trail's `spec_hash` (Step 31). A prompt is its registry ref, or the inline template's digest.
5. **Register.** One entry per agent. Lifecycle status stays in the agent registry (Step 25),
   which changes without commits; the register points to it.
6. **CODEOWNERS is generated** from `governance/approvers.yaml` by tier. The governance files are
   owned by the high-risk approvers.
7. **A CI gate enforces the high-risk rule.** CODEOWNERS only requests reviewers, and requiring
   code-owner review would apply to every tier alike. So a separate check, "high-risk approval",
   fails a pull request until a named `high` approver has approved its latest commit. It applies
   when the pull request touches:
   - a spec that is high-risk before or after the change, including deletion; or
   - a governance file.

   The gate is hardened against a pull request working around it:
   - The workflow runs the base branch's gate with the base branch's approvers, so a pull request
     can't edit the gate or appoint its own approver.
   - Only each reviewer's latest review counts.
   - An approval of an older commit doesn't count.
   - Authors can't approve their own change.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Hand-written cards | Drift from the code within weeks; the spec requires generation |
| Branch protection "require code-owner review" alone | Applies to every path a code owner covers, not by tier; with one maintainer it blocks every pull request. Wins with separate teams per tier |
| An OPA policy over the PR diff (Conftest) | Equal power; another tool to install in CI for a decision that is a few lines of Python with unit tests |
| Rulesets with required reviewers per path (GitHub) | Settings, not code: not reviewable or testable here. Can complement the gate on the Mac |
| Copying the registry's lifecycle status into the register | Stale the moment someone runs `just agents approve` |

## Consequences
- **Owner action.** Make "high-risk approval" a required status check on `main` (repository
  settings). Until then it reports but doesn't block.
- **Single maintainer.** With one maintainer named in every tier, a high-risk change authored by
  that maintainer can't pass the gate: GitHub doesn't allow self-approval, and neither does the
  gate. Add a second person or team before high-risk agents ship.
- **Teams.** `@org/team` entries aren't expanded into members yet, because that needs an org-read
  token. Only named users count.
- **Unlisted MCP server.** The invoice-triage spec's `erp` server isn't in `platform.yaml`, and
  the cards say so. Listing it is the platform owner's decision.
