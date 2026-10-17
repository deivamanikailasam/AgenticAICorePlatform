# Reference app 2: research report with a critic, published only after approval

## What changed
- `agents/research-report` 0.2.0 is now a hybrid workflow with two nodes:
  - `research`: the supervisor team, nested as a subgraph (`workers/team.yaml`, now
    `research-report-team`);
  - `publish`: `local://platform/publish_report`, behind an approval step that the spec
    requires.

  The publish step follows the team only `on_ok`, so a team that fails publishes nothing.
- The team gains a critic (`workers/critic.yaml`). It reads the task, sources, figures and
  report, and writes a review: approved or the issues found.
- New tool `agent_tools.publish.PublishReportTool`:
  - irreversible and tagged `external`;
  - never retried;
  - stores the report in an artefact store, in memory by default;
  - returns where it was published, with the report.

  It loads by its exact ref through the `tool` entry point.
- The risk tier is now medium: publishing is irreversible and reaches people outside the team.
  The system card follows.
- The golden tasks are regenerated:
  - all four workers are required;
  - search, calculator and publish are required;
  - `publish_report` must stop for approval.

  The eval harness registers the tool and approves as the approver. The mechanics baseline is
  re-recorded, and the cards, register, lineage, CODEOWNERS and catalogue are regenerated.
- The Step 22, 25, 30 and 31 tests follow the new shape:
  - the supervisor is `research-report-team`;
  - runs wait for approval;
  - the requester's own approval is refused (403) and another person's publishes;
  - the nested team's delegations and calls are attributed to the run;
  - the trace runs app, then team, then workers.

## Why
Step 40's second reference app needs a supervisor, workers, a critic and approval before
publishing. A worker can't stop a team for a person, so the approval sits after the team, in
the workflow.

## How to check it
- `uv run pytest services/agent-api/tests/test_team_api.py`: the whole flow, through the run
  API.
- `uv run pytest libs/agent-tools/tests/test_publish.py services/agent-api/tests/test_registry_gate.py`.
- `just eval-baseline-check research-report`.

## Risks and follow-ups
- The mechanics baseline is 0/100 on the stand-in: its canned answers can't drive the team.
  Quality needs real models (VERIFY-ON-MAC).
- A durable publishing destination (S3 with credentials from OpenBao) is to be wired per
  deployment.
- Promotion of 0.2.0 to staging and prod follows.
