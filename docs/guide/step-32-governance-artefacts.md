# Step 32 · Governance artefacts

## 1. At a glance

| | |
|---|---|
| **Goal** | Paperwork that can't lie: cards, lineage and a register generated from the code, and a rule that risky changes need the right person's sign-off |
| **Time** | About a day |
| **You end up with** | `libs/agent-governance`; agent cards (20), system cards (7), lineage, the AI-use register, generated `.github/CODEOWNERS`, `governance/approvers.yaml`, the "high-risk approval" CI check |
| **New words** | agent card, system card, lineage, AI-use register, CODEOWNERS, required status check |

## 2. The big idea

Governance documents usually go stale the day after they are written. Here they are build
outputs: change a spec, and the card, register, lineage and ownership file change with it. If
someone forgets to regenerate them, the build fails.

The second idea is that risk decides who signs. A change to a low-risk agent needs a normal
review. A change to a high-risk agent, or to the rules themselves, needs a named person's
approval of the exact final version.

> *Analogy:* a food label printed by the machine that mixes the recipe. It can't list
> ingredients the machine didn't use. For a hospital menu, the dietitian signs off any recipe
> change before it goes out.

## 3. What we're doing

1. Generate an agent card for every spec and a system card for every reference app.
2. Generate lineage: spec → prompts → models → tools → datasets.
3. Generate an AI-use register entry per agent.
4. Generate CODEOWNERS by risk tier, from one approvers file.
5. Add a CI check that blocks high-risk changes until a named approver approves.

## 4. Why we're doing this

Regulators, customers and security reviewers ask the same questions:
- What does this agent do, and who owns it?
- How risky is it, and what can it touch?
- What data does it handle, what are its limits, how was it tested?
- Who approved the last change?

Answering from the code, rather than from a wiki, is the only way the answers stay true.

> *Watch out:* a card that says "tested" with a made-up score is worse than no card. Cards here
> cite only recorded eval runs.

## 5. Key ideas

- **Agent card.** What one agent is and may do: purpose, owner, risk tier, autonomy, models,
  tools (side effect, data, approval), guardrails, oversight, limitations, eval results.
- **System card.** A whole reference app: the lead agent plus its workers, every tool and service
  it reaches, where people are in the loop. It is as risky as its riskiest part.
- **Lineage.** Which spec version used which prompts, models, tools and datasets. The spec hash is
  the one in every audit event, so an event leads back to this graph.
- **AI-use register.** A one-line-per-agent inventory, the list governance frameworks ask
  organisations to keep.
- **CODEOWNERS.** A GitHub file saying who reviews which paths. Here it is generated per spec, by
  tier.
- **The gate.** A CI check that fails until a named high-risk approver approves the latest commit.
  The approvers are read from the base branch, so a pull request can't approve itself.

## 6. How to do it

```bash
just cards            # regenerate every artefact
just cards --check    # in `just check` and CI: fails if anything is stale or orphaned
# who approves what
$EDITOR governance/approvers.yaml && just cards
```

Make the gate binding: in the GitHub repository settings, add a branch protection rule (or a
ruleset) for `main` that requires the status check **high-risk approval**.

## 7. Benefits

- Cards, lineage and the register are always current; the build guarantees it.
- Every claim on a card traces to enforced configuration.
- High-risk changes and rule changes can't slip through: approval is bound to the final commit.
- Audit events, lineage and cards share identifiers.

## 8. Drawbacks and risks

- **Single maintainer.** With one maintainer, a high-risk change they author can't be approved.
  Add a second approver before high-risk agents ship.
- **Owner setting required.** The check blocks only once it is made required in the GitHub
  settings.
- **Teams.** Teams in the approvers file aren't expanded yet; name users.
- **Thin eval data.** Eval results stay "none recorded" until Step 34 records runs.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Generated cards | Hand-written model cards | Never for live systems; fine for a one-off research model |
| CI gate by tier | Code-owner review on every path | Separate teams own different tiers |
| Python gate | Conftest/OPA over the diff | OPA is already the CI policy tool for this |
| Register in the repo | A GRC tool (ServiceNow, OneTrust) | The organisation already keeps its register there; export `cards/register.yaml` to it |

## 10. Which approach fits which situation

- **A reviewer asks "what does this agent touch?":** its agent card.
- **An incident:** the audit event's `spec_hash` → lineage → the exact prompts, models and tools.
- **A compliance review:** the AI-use register and the system cards.
- **Changing a high-risk agent:** open a pull request; the check names who must approve.

## 11. Common mistakes

- Editing a generated card by hand. The next `just cards` overwrites it, and `--check` fails
  until then.
- Lowering a spec to medium "just for this change". The gate treats high before or after as high.
- Listing a team in `high` and expecting its members to count. Name the users.
- Forgetting to make the check required, so it only reports.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| Cards regenerate in CI and fail the build if stale | ✅ | `just cards --check` is part of `just check`, run by CI's check job. `test_a_stale_or_orphaned_card_fails_the_check`: in a copy of the repository, raising a spec to high makes the check fail (card, register, CODEOWNERS), regenerating fixes it, and an orphaned card fails it again. `test_the_cards_on_disk_are_current` |
| A high-risk spec change cannot merge without the named approver | ✅ in code, owner setting pending | `test_governance_gate.py` on a real git history: a high-risk change fails with no review, a stranger's approval, or the approver's approval of an older commit, and passes with the approver's approval of the head commit. A pull request that appoints its author as approver still needs the base branch's approver. 14 decision tests in `test_governance.py` (raise, lower, delete, governance files, latest-review-wins, self-approval). Blocking requires the owner to make "high-risk approval" a required check on `main` |
| Agent and tool cards with the listed fields | ✅ | `test_every_spec_gets_an_agent_card_with_the_required_fields` (20 specs); tool cards since Step 6 |
| System card per reference app; lineage; register; CODEOWNERS by tier | ✅ | `test_a_system_is_as_risky_as_its_riskiest_agent`, `test_lineage_hashes_match_the_audit_trail_versions`, `test_the_register_has_one_entry_per_agent`, `test_codeowners_assigns_each_spec_the_approvers_of_its_tier` |
| No invented numbers | ✅ | `test_eval_results_are_cited_only_from_a_recorded_run` |
| ADR | ✅ | ADR 0043 |

## 13. Recap

The platform's governance paperwork is now a build output. That covers 20 agent cards, 7 system
cards, lineage, the AI-use register and CODEOWNERS, and all of it fails the build when stale. A CI
check turns "high-risk changes need the named approver" into a status GitHub can require; it reads
its rules from the base branch and binds approval to the final commit. Two things are left to
the owner: making that check required, and adding a second approver.
