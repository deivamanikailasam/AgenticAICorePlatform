# ADR 0050: Reuse kit: agent-sdk as the public surface, agentctl over the platform's own run API and release rules, and a copier template for projects that each run the platform from its pinned base and signed images

- **Status:** Accepted
- **Date:** 2026-10-17
- **Step:** 39
- **Deciders:** repository owner (delegated the open decisions, 2026-10-16)

## Context
Step 39 asks for four things:
- a `copier` template for a new agent project (spec, evals, cards, CI, deploy overlay) wired to
  the platform;
- `agent-sdk` (Python) for custom tools, patterns and evaluators;
- `agentctl validate / run / eval / promote`;
- the platform's MCP and A2A endpoints documented for other teams.

Done when a new project made from the template passes CI and runs its first agent in under 30
minutes.

Constraints found while building it:
- **Closed extension points.** The spec's patterns were a closed list; eval checks had no plug-in
  point; the run API refused any `local://` tool that wasn't one of the platform's.
- **Code tied to this repository's layout.** Promotion, the generated deployment config and the
  eval runner all assumed it.
- **Libraries read repository files by path.** These were the policy data and the platform
  config. Installed as wheels from git, as a project installs them, the files weren't there.
  Only rendering a project and running it outside this repository showed it.
- **A base that wasn't self-contained.** The deployment base read a file outside its directory,
  which kustomize refuses for a remote base.

## Decision
1. **`agent-sdk` is the stable public surface.** It provides:
   - `@tool` (a `Tool` from an async function);
   - `PatternBuilder` and the plan types;
   - `Score`, `Observation`, `Task` and `Evaluator`.

   Teams register extensions with entry points:
   - tools: `agent_platform.components`, `tool.<name>`;
   - functions: `agent_platform.functions`;
   - patterns: `pattern_builder.<name>`, used with `pattern: custom` and
     `components.pattern_builder`;
   - evaluators: `agent_platform.evaluators`, named in a task's `expected.custom`.
2. **Extensions stay inside the platform's controls.**
   - A team's tool loads only by the exact `ref` its `ToolSpec` declares and the spec grants.
     Test doubles never load.
   - A team's pattern builder's plan goes through the same approval insertion and checks as
     every plan.
   - Evaluators fail closed: missing, raising or returning the wrong type fails the task.
   - OPA, guardrails, budgets and audit apply to every call, as before.
3. **`agentctl` drives the platform's own code, not a copy.**
   - `run` and `eval` go through the in-process run API with the production assembly (and the
     in-process policy rules, parity-tested with OPA). `run --api` uses a deployed run API.
   - `promote` and `deploy-config` use `ReleaseRepo`, which the platform's own scripts now use.
   - `deploy-check` renders each overlay and applies `deploy_policy`, with the project's
     environment names.
4. **A project runs the platform in its own namespaces, from the platform's pinned base and
   signed images.**
   - The template's overlays come from the platform's own overlays. `just template-data` keeps
     them in step and `just check` fails if they drift.
   - They take the base and shared components remotely, at `platform_ref`.
   - Every environment name is the project's: namespace, OpenBao role and mount, Keycloak
     client.
   - `platform_ref` is the commit whose images the platform's overlays pin, so the SDK
     (a git dependency at that commit) is the code in those images.
   - Third-party versions are constrained to the platform's `uv.lock`, which keeps its 14-day
     cooldown.
   - The project's images are the platform's signed image plus its package (`--no-deps`).
5. **Platform data travels with its packages.**
   - The `agent-tools` and `agent-spec` wheels carry the policy data and the default config.
   - The repository's files still win in this workspace.
   - The base mounts a generated copy of the data-lifecycle policy.
6. **The 30 minutes are measured, cold, in CI.**
   - `template-e2e` renders a project and runs it with uv's cache off against the commit on
     GitHub: lock, sync, the project's CI checks, its first agent's run and eval, the promotion
     rules and the deployment rules. Each step is timed.
   - The first agent needs no model and no services, so the measurement is the platform's, not
     a model's.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| One shared platform installation that hosts every team's agents | Teams' code would have to be built into one image, and one team's release would redeploy everyone. It wins for spec-only agents (tools from MCP servers): those can be promoted into the platform's own `deploy/releases` |
| Cookiecutter instead of copier | No `copier update`: projects couldn't take later template changes |
| Publishing the platform to a package index | Needs a registry and a release process; a git pin at the signed commit gives the same reproducibility now |
| An open string for `pattern:` | Every check keyed on the pattern enum would need a default; `custom` plus the existing `components` slot reuses what validation already does |
| Copier tasks to run `uv lock` after rendering | Copier would run code from the template on the person's machine (`--trust`). The README's first command does it instead |

## Consequences
- **Good:**
  - A team starts from a working, tested project in its own repository and namespaces.
  - In CI, cold, the whole first run took 36.6 s (`template-e2e` run 38998050472: lock 2.3 s,
    sync 3.8 s, first run 4.6 s, eval 4.9 s, deploy-check 6.9 s).
  - The time a person needs to read the README and answer copier's questions is not measured.
- **Costs:**
  - Each project runs its own copy of the platform's services; staging and prod need their
    Project 0 resources per project (OpenBao role and mount, Keycloak client).
  - A project's image must be rebuilt to take a new platform version.
  - The SDK's surface is now a contract: changes need a deprecation path.
- **Follow-ups:**
  - Project image build and signing workflow in the template.
  - `copier update` to move a project to a newer platform pin.
  - A shared-platform path for spec-only agents.
