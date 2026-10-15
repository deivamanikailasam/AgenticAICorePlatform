# Governance artefacts: agent and system cards, lineage, the AI-use register and CODEOWNERS

**Step:** 32 · **Commit:** feat(agent-governance): cards, lineage, AI-use register and CODEOWNERS by risk tier · **Date:** 2026-10-15

## What changed
New library `libs/agent-governance`, used by `just cards`:
- **Agent cards.** One per spec (20), in `cards/agents/*.yaml` and `docs/cards/agents/*.md`. Each lists:
  - purpose, owner, risk tier, autonomy, pattern, runtime
  - the model alias per role
  - tools, with side effect, data classification and approval
  - data handled, guardrails (the always-on baseline first), human oversight and limitations
  - eval results
- **System cards.** One per reference app (the 7 top-level agents and their workers), in `cards/systems/` and `docs/cards/systems/`. Each covers the whole system's agents, tools (with which agent uses each), services, oversight and limits. A system is rated as risky as its riskiest agent.
- **Lineage.** `cards/lineage.yaml` and `docs/cards/lineage.md` (Mermaid) trace spec → prompts (registry refs, or the inline template's digest) → models → tools → datasets, through workers. Spec hashes equal the `spec_hash` in audit events (Step 31).
- **AI-use register.** One entry per agent, in `cards/register.yaml` and `docs/governance/ai-use-register.md`. Lifecycle status is left to the agent registry (`just agents`), because it changes without a commit.
- **CODEOWNERS.** `.github/CODEOWNERS` is generated from `governance/approvers.yaml`: each spec is owned by the approvers of its risk tier, apps by their system's tier, and the governance files by the high-risk approvers.
- **`codeowners.decide`.** The merge gate's decision (wired into CI in the next commit).
- **Check.** `just cards --check` (part of `just check`, so it runs in CI) now covers every artefact, including orphaned files.

## Why
Step 32: cards, lineage and a register "generated from specs and registries", and change approval by risk tier. Every field is derived from configuration the platform enforces, so a card can't claim more than runs.

Two rules keep the cards honest:
- **Eval results** are cited only from a recorded run (`evals/results/<agent>.json`, which Step 34 writes). With none recorded, the card says "No recorded eval run".
- **Unlisted MCP servers.** `erp` isn't in `platform.yaml`'s MCP allowlist; its side effects are set only by the invoice-triage demo. The cards say "undeclared" and name the gap rather than guessing.

## How to check it
- `just cards --check` reports "up to date".
- `uv run pytest libs/agent-governance` (23 tests): required fields on every card; eval results only from a recorded run; system risk is the maximum of its agents; unlisted servers are called out; lineage hashes equal the audit trail's `spec_hash`; one register entry per agent; CODEOWNERS per tier; 13 gate decisions plus self-approval.
- In a copy of the repository, raising a spec to high makes `--check` fail (card, register, CODEOWNERS), and an orphaned card fails it too.

## Risks and follow-ups
- Owner decision: there is a single maintainer, so every tier names `@deivamanikailasam`. GitHub won't let anyone approve their own pull request, so add a second person or a team to `high` before high-risk agents ship.
- The invoice-triage spec uses an MCP server that isn't in `platform.yaml`. Listing it there (with its side effects) is the platform owner's call.
