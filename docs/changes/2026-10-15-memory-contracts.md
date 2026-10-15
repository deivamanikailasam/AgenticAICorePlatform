# Memory contracts: private scopes, erasure of data about a person, versioned instructions

**Step:** 20 · **Commit:** feat(agent-contracts): tighten memory scopes, erasure and versioning · **Date:** 2026-10-15

## What changed
- `MemoryScope.sees`: an item is visible only when each of its own scope fields (user, agent)
  is empty or equals the query's. Before, a tenant-wide query could see every user's personal
  memories.
- `Provenance.subject_user_id` records whose personal data an item holds. Right to erasure
  removes those items too, not only items stored under the user's scope.
- `MemoryItem.key` and `version`: procedural memory (learned instructions) is versioned like
  prompts. The store assigns the version, search returns only the newest, and older versions
  stay readable for audit.
- New contract tests for every memory store: scope privacy, erasure of items about a person,
  procedural versioning, and expired items never returned.
- New contract test for every memory policy: untrusted content can never become procedural
  memory, even when marked validated (OWASP ASI06).
- The in-memory and SQLite dummies, and the validate-and-redact dummy policy, now meet the new
  contracts.

## Why
Step 20: a memory store is a persistent prompt-injection channel and a store of personal data.
These rules have to hold for every implementation, so they live in the contract.

## How to check it
`uv run pytest libs/agent-contracts/tests/test_dummies_pass_contracts.py -k memory`

## Risks and follow-ups
Tenant-wide queries now see less than before. A caller that relied on the wide view must query
per user.
