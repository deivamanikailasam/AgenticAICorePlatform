# The agent-project template

## What changed
- `copier.yml` (root) and `templates/agent-project/` hold the copier template (copier 9.18.2,
  a dev dependency) for a new agent project. A project gets:
  - a deterministic first agent with a function, its own `@tool` and its own evaluator;
  - 5 golden tasks, unit tests and an agent card;
  - CI: ruff, pytest, `agentctl validate`, `eval`, the promotion rules and `deploy-check`;
  - CODEOWNERS and a Dockerfile on the platform's signed image;
  - overlays for dev, staging and prod on the platform's base at a pinned commit;
  - the promotion files.
- `scripts/template_data.py` (`just template-data`, checked by `just check`) derives from the
  platform:
  - the platform pin, its images and its third-party constraints;
  - the overlays;
  - the generated deployment config.
- `scripts/template_e2e.py` (`just template-e2e`) and the `template-e2e` workflow make a project
  and take it through its first 30 minutes, timing each step.
- copier's dependencies dunamai and wcwidth are pinned to their newest releases older than 14
  days: 1.26.2 and 0.9.1.

## Why
Step 39: a new project made from the template passes CI and runs its first agent in under 30
minutes.

## How to check it
- `just template-data-check`: the generated files are up to date.
- `just template-e2e --local`: every step passes against this checkout.
- The `template-e2e` workflow measures the same flow cold, against the commit on GitHub.

## Risks and follow-ups
- `platform_ref` follows the image pin. Until the overlays pin images that contain the SDK's
  packaging fixes, a project made with the defaults must pass `--data platform_ref=<newer
  commit>`. The next image re-pin closes this.
- The project's images and their signing are the team's to run. The README says how.
