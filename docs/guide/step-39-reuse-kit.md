# Step 39 · Reuse kit

## 1. At a glance

| | |
|---|---|
| **Goal** | Let another team start a new agentic application on the platform in minutes, in its own repository, with its own code, held to the platform's rules |
| **Time** | About two days |
| **You end up with** | `agent-sdk` (`@tool`, pattern builders, evaluators); `agentctl validate / run / eval / promote / deploy-config / deploy-check`; a copier template that makes a working project (first agent, golden tasks, card, CI, overlays, promotion files); `just template-e2e` and its CI workflow, which time a new project's first run; an endpoint reference for other teams |
| **New words** | SDK, entry point, copier template, platform pin, constraint, remote base, fail closed |

## 2. The big idea

A team that wants agents shouldn't fork the platform or wait for its maintainers. It runs one
command and gets its own repository. In it is a small agent that already works, tests that
already pass, and a CI pipeline that holds it to the same rules the platform holds itself to.
The team writes its tools, steps and checks in its own package. The platform loads them only in
the ways it allows. Everything the platform guarantees still happens around them: policy on every
call, approvals for what can't be undone, budgets, audit, and promotion through staging and a
canary.

> *Analogy:* a franchise kit. A new branch gets the fitted-out kitchen, the recipes, the
> suppliers and the inspection checklist on day one. It can add its own dishes, but they're
> cooked in the same kitchen, by the same rules, and inspected the same way.

## 3. What we're doing

1. Open the platform's extension points: custom patterns (`pattern: custom`), custom eval checks,
   and a team's own tools in the run API.
2. Package the stable surface as `agent-sdk`, and give it a command line, `agentctl`.
3. Turn promotion and the generated deployment config into a library both the platform and a
   project use.
4. Write a copier template whose platform-specific parts are generated from the platform itself.
5. Make the platform installable from git: its wheels carry the files they read, and its
   deployment base builds on its own.
6. Prove it: make a project, cold, in CI, and time it from the first command to a passing first
   run.
7. Document the endpoints other teams call.

## 4. Why we're doing this

Until now, a new agent meant a change to this repository. That doesn't scale past one team. It
also mixes everyone's code, reviews and releases. The reuse kit separates them: each team owns its
repository and release pace, and the platform owns the runtime, the controls and the rules.
Separation only helps if the rules can't be skipped, so every extension point keeps the controls
around it.

> *Watch out:* "it works in the platform's repository" isn't "it works for a team". Rendering a
> project and running it from installed packages found two problems no test inside the repository
> could show: libraries reading files by repository path, and a base that read a file outside its
> directory.

## 5. Key ideas

- **Entry points.** A Python package says "I provide a tool called X" in its `pyproject.toml`.
  The platform discovers it when it's installed. There's no registration code and no fork.
- **Extensions stay inside the controls.**
  - A team's tool loads only by the exact `ref` it declares and the spec grants.
  - A team's pattern builder makes a plan that goes through the same approval insertion and
    checks.
  - A team's evaluator fails closed: if it's missing, raises or returns nonsense, the task fails.
- **One code path.** `agentctl run` and `agentctl eval` use the platform's run API in process,
  with the production assembly. `agentctl promote` uses the same release library as
  `just promote`. Nothing is re-implemented for projects.
- **Pins that match.**
  - A project's SDK is a git dependency at the commit whose signed images the platform deploys.
  - Its third-party versions are constrained to the platform's lock (and its 14-day cooldown).
  - Its overlays take the platform's base from the same commit.
- **Generated, not copied.** The template's overlays, pins and deployment config are generated
  from the platform. `just check` fails when they fall behind, so the template can't drift.

## 6. How to do it

```bash
# make a project (any machine with uv)
uvx copier@9.18.2 copy gh:deivamanikailasam/AgenticAICorePlatform my-project
cd my-project && uv lock && uv sync
uv run agentctl validate --strict
uv run agentctl run my-project-triage --input '{"request": "I am locked out"}'
uv run agentctl eval my-project-triage
uv run agentctl deploy-check            # needs kustomize; renders against the platform's base

# in the platform
just template-data          # regenerate the template's platform-derived files
just template-e2e           # make a project and time its first 30 minutes (CI: template-e2e)
just template-e2e --local   # the same against this working tree
```

**Writing a tool** (`src/<package>/tools.py`), then register it in `pyproject.toml`
(`"tool.<project>.<name>" = "<package>.tools:<function>"`) and grant it in `agent.yaml`
(`tools: [{ref: local://<project>/<name>}]`):

```python
@tool(
    name="lookup_policy",
    ref="local://claims-desk/lookup_policy",
    description="The service policy for a request category.",
    input_schema={...},
    side_effect=SideEffect.READ,
)
async def lookup_policy(arguments, ctx): ...
```

## 7. Benefits

- **A team starts from something that works.** The first run of a new project passed every check
  in 36.6 s of machine time in CI, cold.
- **The platform's rules travel with the project:** its CI runs the platform's spec validation,
  eval checks, promotion rules and deployment rules.
- **No forks.** Teams extend through the SDK. Platform improvements reach them by moving the pin.
- **Teams are kept apart:** their own namespaces, OpenBao roles and mounts, and Keycloak clients.

## 8. Drawbacks and risks

- **Each project runs its own copy of the platform's services.** That's simple and isolated, but
  it costs more than a shared installation. For spec-only agents, a shared one would do (ADR 0050).
- **The SDK is now a contract.** Changing a name in `agent_sdk` breaks teams; it needs a
  deprecation path.
- **A project's images are its own to build and sign.** The template documents how, but doesn't
  yet ship that workflow.
- **The 36.6 s is machine time only.** A person reading the README and answering copier's
  questions adds their own time, which isn't measured.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Copier | Cookiecutter | You never need to update projects from a newer template |
| A project per team, running the platform from a pinned base | One shared platform hosting all agents | Agents made only of specs and MCP tools, with no team code |
| Git pin of the SDK | A private package index | Many teams and frequent releases; an index makes versions easier to browse |
| `pattern: custom` + `components.pattern_builder` | An open string for `pattern` | Never here: the closed list keeps validation precise |
| In-process run API for `agentctl run` | A local docker compose of the platform | Debugging something that needs Postgres, Temporal or Keycloak; then use `--api` |

## 10. Which approach fits which situation

- **A team with its own tools or steps:** a project from the template.
- **An agent built only from platform tools and MCP servers:** a spec in the platform's
  `agents/` may be enough.
- **Calling the platform from an existing application:** the run API (`docs/reference/endpoints.md`).
- **Calling it from another agent framework:** A2A or MCP, same reference.
- **A new reasoning pattern:** a `PatternBuilder` in the team's package, `pattern: custom`.
- **A domain-specific quality check:** an evaluator, named in the golden tasks' `expected.custom`.

## 11. Common mistakes

- Granting a tool by a `ref` that its `ToolSpec` doesn't declare exactly. The run API refuses it.
- Marking a tool that sends or pays as `read` or `write`. Declare `irreversible`, and the
  platform makes it wait for a person.
- Editing the template's generated files by hand. Run `just template-data`.
- Pinning `agent-sdk` to a commit whose images the environments don't run.
- Treating `--provider stand-in` results as quality. Only `gateway` runs measure models.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| A new project from the template passes CI and runs its first agent in under 30 minutes | ✅ | `template-e2e` run 38998050472 on a818a63, cold (uv cache off), project installed from GitHub at that commit, passed in 36.6 s against the 1,800 s limit. Steps: copier 1.7 s; `uv lock` 2.3 s; `uv sync` 3.8 s; ruff check and format 0.1 s each; pytest 2.1 s; `agentctl validate --strict` 8.7 s; first run 4.6 s (`succeeded`); `agentctl eval` 4.9 s (5 of 5 golden tasks, no model calls); promotion rules 1.5 s; `deploy-check` against the platform's remote base 6.9 s. Run 38998609506 on 78a8312 passed again. A person's reading and answering time isn't included |
| `copier` template (spec, evals, cards, CI, deploy overlay) wired to the platform | ✅ | `copier.yml`, `templates/agent-project/`; `test_template.py` (3 passed): pins match the images the environments run, the project's own environment names, nothing left unrendered, the template data current |
| `agent-sdk` for custom tools, patterns and evaluators | ✅ | `libs/agent-sdk`. Tests: a team's pattern builder still gets the approval gate; an unknown builder is refused (2); evaluators score and fail closed (2); an installed tool loads by its exact ref and an undeclared ref is refused (2) |
| `agentctl validate / run / eval / promote` | ✅ | `test_agentctl.py` (5 passed): validate, run (`HELLO, ADA` through an SDK tool), eval 1/1, promote to staging, deploy-config check, a failing task fails the eval, usage errors, deploy-check refuses a NodePort |
| MCP and A2A endpoints documented for other teams | ✅ | `docs/reference/endpoints.md` |
| Threat model updated | ✅ | Reuse kit component, T57 to T60, control C27; `just threat-check`: 60 threats, 13 components, 27 controls, 75 tests, 0 problems |
| ADR | ✅ | ADR 0050 |

## 13. Recap

Another team can now start an agentic application on the platform with one command. It gets a
repository with a working agent, its own tool, step and quality check, tests, an agent card, CI,
and overlays for three environments, all held to the platform's own rules. Its code is plugged in
through a small SDK. The platform loads that code only in ways that keep policy, approvals, budgets
and audit around it. Measured cold in CI, a new project went from the first command to a passing
first run, evaluation and deployment check in 36.6 seconds of machine time.
