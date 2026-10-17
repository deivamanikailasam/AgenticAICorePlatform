# Architecture views: C4, approvals and A2A

## What changed
- `docs/architecture/c4.md` has four views:
  - context: who uses the platform, and Project 0;
  - containers: the five services, Temporal and Project 0's services;
  - the run API's components;
  - deployment: Git, Argo CD and the namespaces.
- `docs/architecture/approval-sequence.md` shows an irreversible action waiting for a person.
- `docs/architecture/a2a-sequence.md` shows another agent calling a published agent.
- The architecture overview links every page.

## Why
Step 40 asks for the final architecture docs. The overview had only layers, the roadmap and
the Project 0 connection, and it promised the detailed views later.

## How to check it
Every arrow matches code or configuration:
- `deploy/base/networkpolicies.yaml` for which service talks to which;
- `services/agent-api/src/agent_api/desk.py` and `policies/agent/` for the approval path;
- `services/a2a-gateway/src/a2a_gateway/app.py` for A2A.

The diagrams render on GitHub.

## Risks and follow-ups
The diagrams are drawn by hand, so they can fall behind the code. The threat model's data
flows (`docs/security/`) are the checked source for trust boundaries.
