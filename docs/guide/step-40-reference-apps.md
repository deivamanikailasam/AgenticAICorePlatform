# Step 40 · Reference apps and wrap-up

## 1. At a glance

| | |
|---|---|
| **Goal** | Prove the platform runs any pattern with three working applications, and close the build with its catalogue, runbooks, architecture, measured results and a retrospective |
| **Time** | About two days |
| **You end up with** | Three reference applications: a knowledge assistant (agentic RAG on `rag-core`); a research report written by a team of agents, reviewed by a critic and published only after a person approves; invoice triage. Also a generated results table, C4 and sequence diagrams, kill-switch and app runbooks, and ADR 0051 |
| **New words** | Reference application, subgraph, critic, mechanics baseline, results table, retrospective |

## 2. The big idea

A platform that claims to run "any" agent pattern should show it. This step builds three
applications, each a different shape, and runs them all on the same engine under the same
rules:
- **The knowledge assistant** looks things up and reasons over what it finds.
- **The research report** is a small team. A researcher, an analyst and a writer produce a
  report, a critic checks it, and a person approves it before it's published.
- **Invoice triage** checks invoices by fixed rules, lets an agent handle the odd ones, and pays
  only after a person approves.

None of them needs special code in the platform.

Then the build is wrapped up honestly: every number reported is one a run measured, and what
hasn't been measured is said plainly.

> *Analogy:* a kitchen that claims it can cook any cuisine serves three very different dishes
> from the same stoves, under the same hygiene rules. The menu lists only what was actually
> cooked and tasted, not what the chef expects it to taste like.

## 3. What we're doing

1. Write the knowledge assistant's spec, its 100 golden tasks and its eval harness, and promote
   it to prod.
2. Rebuild the research report:
   - the team becomes a subgraph, with a critic added;
   - a `publish_report` tool is added, marked irreversible so it waits for a person;
   - it goes to prod as a 10% canary.
3. Make a nested spec run as its own agent (its own model, tools and identity) inside the
   parent's controls.
4. Pin the environments to the images that contain this code.
5. Generate the results table from recorded results only.
6. Write the architecture views, the kill-switch and app runbooks, and the retrospective ADR.

## 4. Why we're doing this

Patterns proven only in unit tests can hide gaps. Building real applications found one: a nested
spec ran with its parent's model, tools and identity. A worker could have used a tool its own
spec didn't grant, and its spend and approvals weren't its own. Fixing it made every
composition boundary a privilege boundary.

The results table exists because numbers drift. A table generated from the files the runs wrote
can't say more than the runs measured.

> *Watch out:* a stand-in model answering with canned responses makes "0 of 100 succeeded" look
> like a disaster and "100 of 100" look like a triumph. Neither says anything about quality.
> They're mechanics: the harness and the platform worked.

## 5. Key ideas

- **Specs, not code.** Each application is an `agent.yaml` (plus worker specs). The platform's
  compiler, runtime and controls do the rest.
- **A subgraph is an agent.**
  - research-report's `research` node runs the team spec as its own agent: its own name,
    version, model and tools, with run ID `<run>/research`.
  - It still uses the parent's policy, admission, budgets, approvals, memory and audit.
- **The critic reviews but can't publish.** Only the app's `publish` step has the
  `publish_report` tool, and that tool waits for a person who isn't the requester.
- **`on_ok` edges.** `research -> publish` runs only if the team succeeded. A failed team never
  publishes.
- **Mechanics vs quality.** Stand-in runs gate regressions in the harness and platform. Quality
  needs real models (VERIFY-ON-MAC).
- **Measured, or not shown.**
  - `docs/results.md` reads the committed result files, plus CI numbers recorded with their
    workflow run IDs.
  - It lists quality as not measured.

## 6. How to do it

```bash
just validate agents/                    # the three apps' specs (and the rest)
just eval-app knowledge-assistant        # 100 tasks, stand-in: mechanics only
just eval-app research-report
just eval-app invoice-triage
just eval-gate                           # every app's gate (CI does this on every push)
just promote research-report --to prod   # a later release goes in as a 10% canary
just results                             # regenerate docs/results.md
# on the Mac, with real models (records quality baselines):
just eval-app research-report --provider gateway --publish
```

## 7. Benefits

- **Three patterns on one engine,** with no app-specific runtime code: agentic RAG, a supervisor
  team with a critic, and a hybrid workflow.
- **Irreversible actions wait for a person.** The two apps that have one (publishing a report,
  paying an invoice) can't skip the wait.
- **One trace per run:** the app, the team and every worker, each call under the agent that made
  it.
- **Honest results.** Every number in `docs/results.md` links to the run or file it came from.

## 8. Drawbacks and risks

- **No quality numbers yet.** The mechanics baselines are 0 of 100 for the knowledge assistant
  and the research report, because their tasks need a real model's answer. Until real-model
  baselines exist, the gates catch broken mechanics, not worse answers.
- **The team's gain over one agent is unmeasured.** ADR 0051 keeps the team for least privilege
  and review, and sets the rule for deciding once real-model runs exist.
- **Nothing has run against the real Project 0.** Everything here ran against the stand-in.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| A team with a critic for the report | One agent with a checklist | If real-model runs show no gain from the team (ADR 0051's rule) |
| A nested spec as its own agent | Children sharing the parent's model and tools | Never: it breaks least privilege |
| A generated results table | A hand-written table | Never here: numbers drift from their runs |
| `publish_report` as a platform tool | Publishing through an MCP server | When reports go to a real document system with its own API |

## 10. Which approach fits which situation

- **Answers from documents, with sources:** agentic RAG (knowledge-assistant).
- **Work that splits into roles, where review matters:** a supervisor team as a subgraph, with
  a critic and an approval before anything leaves (research-report).
- **Mostly fixed rules with exceptions:** hybrid, with functions for the rules and an agent only
  for the exceptions (invoice-triage).
- **Anything irreversible:** mark the tool `irreversible`. The platform makes it wait for a
  person.

## 11. Common mistakes

- Granting a worker a tool "because the app has it". Each spec gets only what it uses.
- An unconditional edge after a team. Use `on_ok`, or a failure flows into the next step.
- Reading stand-in success rates as quality.
- Editing `docs/results.md` by hand. Change the result files or `docs/results/ci-evidence.yaml`
  and run `just results`.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| Three reference applications: agentic RAG on rag-core; multi-agent research and report (supervisor, workers, critic, approval before publishing); hybrid invoice triage (deterministic validation, agentic exceptions, approval before payment) | ✅ | `agents/knowledge-assistant`, `agents/research-report` (with `workers/team.yaml` and the critic), `agents/invoice-triage`. `test_team_api`: the report waits for approval, self-approval is refused, the approver's approval publishes, each agent has only its own tools. `test_a_nested_spec_runs_under_the_parent_s_controls`. `test_telemetry`: one trace, app to team to workers |
| All three apps pass their gates | ✅ (mechanics) | CI run 39006801714 on 46b850a: all 8 apps pass, knowledge-assistant (low), research-report and invoice-triage (medium) among them; the red team held 57 of 57 attacks. Quality, AUD and latency ceilings are `n/a` on stand-in runs: VERIFY-ON-MAC |
| The results table holds only measured numbers with set sizes | ✅ | `docs/results.md`, generated by `just results` from committed files and CI runs with their IDs; `just results-check` in `just check` |
| Final catalogue, runbooks, architecture docs | ✅ | `docs/reference/components.md` (checked current); `docs/runbooks/kill-switch.md`, `reference-apps.md`; `docs/architecture/c4.md`, `approval-sequence.md`, `a2a-sequence.md` |
| Retrospective ADR | ✅ | ADR 0051 |
| The guide's roadmap shows 40 of 40 | ✅ | `docs/architecture/README.md` roadmap and `PROGRESS.md` |

## 13. Recap

Three applications, each a different shape, now run on one platform under one set of rules. A
team of agents writes and reviews a report, and a person publishes it. An assistant answers from
documents the user may read. A workflow pays invoices only after approval. Building them found
and fixed one real gap: a nested agent ran with its parent's powers. The build closes with a
results table that shows only what was measured. On the stand-in, the platform's mechanics,
controls and deployment are proven. How well the agents answer on real models is the next
thing to measure.
