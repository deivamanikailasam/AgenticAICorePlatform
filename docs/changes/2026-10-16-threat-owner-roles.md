# Threat owners by role

## What changed
- `governance/threat-model.yaml`: the four broad owner roles are replaced by eight roles, each
  with a stated responsibility and a person: security architect, AI safety lead, privacy
  officer, SRE lead, platform lead, release manager, audit and compliance lead, finance controls
  owner. All eight are held by @deivamanikailasam for now. Every threat is reassigned to the
  role that matches it.
- `agent_governance.threats`: an owner can be a plain handle or `{person, responsibility}`. The
  check still requires every role to name a person or team (`@...`). The generated threat model
  gains an Owners table: role, holder, responsibility, threats.
- ADR 0048 updated; new test for the role form.

## Why
Owner decision: one maintainer holds every role today, but the roles are kept separate so each
can be handed to a different person later without re-triaging the threats.

## How to check it
`just threat-check` (49 threats, 0 problems); `uv run pytest libs/agent-governance/tests/test_threats.py`;
see the Owners table at the top of `docs/security/threat-model.md`.

## Risks and follow-ups
- One person holding every role means no separation of duties (for example, the security
  architect also approves releases). Split roles as people join; release approval needs a second
  approver anyway (open owner action).
