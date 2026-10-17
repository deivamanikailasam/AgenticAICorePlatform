# The agent-project template (Step 39)

A new repository for a team's agents, wired to this platform:
- a spec, with its functions, tool and evaluator, built with `agent-sdk`;
- golden tasks, an agent card and CI;
- overlays for dev, staging and prod;
- the promotion files.

```bash
uvx copier@9.18.2 copy gh:deivamanikailasam/AgenticAICorePlatform my-project
```

Copier reads `copier.yml` at the repository root. That file includes `questions.yml` (what it
asks) and `platform.yml` (hidden answers), then renders `template/`. The template has no copier
tasks, so it runs no code on the person's machine.

## What comes from the platform

`just template-data` writes these, and `just check` fails when they are stale:
- `platform.yml`:
  - `platform_ref`: the commit whose signed images the platform's overlays pin. The project's
    `agent-sdk` is a git dependency at that commit, so its code matches those images.
  - `platform_images`: the images those overlays pin.
  - `platform_constraints`: the third-party versions in `uv.lock`.
- `template/platform.yaml` and `template/deploy/promotion.yaml`: copies.
- `template/deploy/overlays/*`: the platform's overlays, with the base and shared components
  taken remotely at `platform_ref`, and the environment names made the project's own.
- `template/deploy/components/head-specs`, `released/` and `releases/`: what
  `agentctl deploy-config` writes for the first agent.

Everything else under `template/` is written by hand.

## Checking it

`just template-e2e` makes a project and takes it through its first 30 minutes, timing each step:
1. `uv lock` and `uv sync`;
2. the project's CI checks;
3. `agentctl run` and `agentctl eval` of the first agent;
4. the promotion and deployment rules.

CI runs it in the `template-e2e` workflow, cold, against this commit on GitHub. `--local` builds
the platform from the working tree instead.
