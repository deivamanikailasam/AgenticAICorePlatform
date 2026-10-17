# ADR 0051: Reference applications as plain specs on the platform, nested specs as their own agents under the parent's controls, a results table of measured numbers only, and a retrospective of the build

- **Status:** Accepted
- **Date:** 2026-10-17
- **Step:** 40
- **Deciders:** repository owner (delegated the open decisions, 2026-10-16)

## Context
Step 40 asks for three reference applications that prove the "any pattern" claim:
1. an agentic RAG knowledge assistant on `rag-core`;
2. a multi-agent research and report generator (supervisor, workers, critic, approval before
   publishing);
3. a hybrid invoice-triage workflow (deterministic validation, agentic exception handling,
   approval before payment).

It also asks for the final catalogue, runbooks, architecture docs, a measured results table and
a retrospective ADR. Done when:
- all three apps pass their gates;
- the results table holds only measured numbers with set sizes;
- the guide's roadmap shows 40 of 40.

The build guide also says a multi-agent design needs an ADR that justifies the extra agents with
measured gains over a single agent.

Constraints:
- This build runs in a cloud container without Project 0 or real models (ADR 0011). Every eval
  here runs against the stand-in gateway, which answers with canned responses.
- Building research-report showed that a nested spec (a subgraph) ran on its parent's model,
  tools and identity. That is wrong in two ways: a worker could use a tool the parent was
  granted but it wasn't, and a child's spend and approvals weren't its own.

## Decision
1. **The apps are specs, not code.**
   - `knowledge-assistant` 0.1.0 uses `agentic_rag` over `mcp://rag-core/search` (on the user's
     behalf). It is low risk and published over A2A.
   - `research-report` 0.2.0 is `hybrid`:
     - a `research` subgraph runs the team spec: a supervisor with researcher, analyst, writer
       and critic;
     - then `publish` calls `local://platform/publish_report`, which is irreversible, so the
       compiler puts an approval in front of it;
     - the edge between them is `on_ok`, so a failed team never publishes;
     - it is medium risk.
   - `invoice-triage` 0.3.0 (Step 15) already met the spec.

   The only new code is the `publish_report` tool, the knowledge assistant's task generator and
   its eval harness.
2. **A nested spec runs as its own agent, inside the parent's controls.**
   - The child gets its own name, version, model, tools and run ID (`<run>/<node>`).
   - It inherits the parent's memory, artefacts, authorisation (OPA), admission (only approved
     versions in staging and prod), budgets and approvals.
   - A worker can never use a tool its own spec doesn't grant.
3. **The multi-agent design is kept, and its quality gain is recorded as not measured.**
   - The team stays for reasons that hold whatever a model scores:
     - each worker has only the tools it needs (least privilege);
     - the critic can read the report but can't publish it;
     - publishing needs a person.
   - The guide asks for measured gains over a single agent. They can't be measured here.
   - The rule for when it is measured on real models (VERIFY-ON-MAC): run research-report and a
     single-agent spec with the same tools on the same 100 tasks. If the team's success rate
     isn't higher by more than the medium tier's allowed drop (2 percentage points,
     `evals/gates.yaml`), fold the team back into one agent and keep the critic as a
     deterministic check.
4. **The results table is generated.**
   - `just results` writes `docs/results.md` from the recorded result files and from
     `docs/results/ci-evidence.yaml` (numbers from CI logs, each with its workflow run ID).
   - `just check` fails when the table is stale.
   - Quality on real models is listed as not measured, never estimated.
5. **"Pass their gates" means the CI eval gates, at the gates' current scope.** On the stand-in,
   the gates check four things:
   - regressions against the mechanics baseline;
   - red-team findings;
   - token growth;
   - AUD growth.

   The quality, AUD and latency ceilings are `n/a` until real-model baselines exist. All three
   apps pass them in CI; the results table links the run.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| A single agent for the research report | Simpler and probably cheaper. It wins if the real-model comparison in decision 3 shows no gain. The critic would then become a deterministic check |
| Children sharing the parent's model and tools (as before) | Breaks least privilege and makes spend and approvals unattributable |
| Writing the results table by hand | Numbers drift from the files they came from; the build's rule is no number without its run |
| Reporting stand-in success rates as quality | They measure canned responses, not models; reporting them as quality would be an invented number in all but name |

## Consequences
- **Good:** the three patterns (agentic RAG, a supervisor team with a critic, a hybrid workflow)
  run on one platform, with no app-specific runtime code. The two with an irreversible action wait
  for a person before it. One trace holds the app, the team and its workers.
- **Costs:** the mechanics baselines say little: 0 of 100 for knowledge-assistant and
  research-report, because their tasks need a real model's answer. Until real-model baselines
  exist, the gates catch broken mechanics, not worse answers.
- **Follow-ups (owner, VERIFY-ON-MAC):**
  - real-model baselines for every app;
  - the single-agent comparison in decision 3;
  - human labels for judge calibration;
  - a second approver for prod promotions and cost approvals.

## Retrospective of the build (Steps 1 to 40)
What the build produced: 40 steps, 51 ADRs and a chapter per step, all on one branch from
2026-10-15 to 2026-10-17. The figures that were measured are in `docs/results.md`.

**What worked**
- **A stand-in for Project 0 with the same upstream images.** It made every integration test,
  the red-team suite and the eval gates runnable in CI. The cost is that nothing has run against
  the real Project 0 yet; that is the largest open risk.
- **Checked, generated artefacts.** The catalogue, schema reference, cards, CODEOWNERS, threat
  model, deployment config, template data and results table are all regenerated and compared in
  `just check`. None of them can drift silently.
- **Controls in one place.** Every tool call goes through one executor, behind policy,
  guardrails and approvals. Adding a component (A2A, MCP proxying, a team's own tool) never
  needed its own security path.
- **Committing only after `just check` passes on the exact staged tree.** After the early CI
  failures (open issues 5 and 6), commits stopped breaking CI on checks that run locally.

**What went wrong, and the lesson**
- **Bugs that only showed in the consumer's shape.**
  - Real-cluster checks found an OpenBao client reused across event loops, and a nested
    `asyncio.run` in the A2A gateway.
  - The cold template check found libraries reading repository files by path, and a Dockerfile
    that didn't copy the policy data.

  Unit and integration tests in the repository passed throughout. Lesson: test end to end in
  the shape the code is used in (cluster, wheel, fresh project), and do it early.
- **A nested spec ran with its parent's model, tools and identity** until research-report's
  tests showed it (decision 2). Lesson: every composition boundary is a privilege boundary, and
  needs a test that a child can't do what its parent can.
- **Quality is unmeasured.** Every eval here is mechanics. The harness, gates and baselines are
  ready for real-model runs, but no number in this repository says how well an agent answers.

## Verification
- **App tests:**
  - `test_team_api`: research-report waits for approval, the requester's self-approval is
    refused, the approver's approval publishes, and each agent has only its own tools.
  - `test_a_nested_spec_runs_under_the_parent_s_controls`.
  - `test_telemetry`: one trace, app to team to workers.
  - `test_publish.py`.
- **CI gates:** all eight apps pass, research-report at medium risk, in CI run 39006801714 on
  46b850a. That run held 57 of 57 red-team attacks and passed 171 integration tests.
- `just results-check`.
