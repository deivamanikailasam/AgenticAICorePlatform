# Strategy

Why the platform is built the way it is, what it bets on, and how to take it from here. The
decisions themselves are in [design-decisions.md](design-decisions.md). What has been measured
is in [results.md](../results.md).

## The problem

Every team that builds with agents rebuilds the same things: model access, tool calling,
memory, approvals, audit, evaluation and deployment. Each copy has its own gaps. Agents act in
the world, so a gap isn't only a bug. It's an agent paying the wrong invoice, leaking another
customer's data, or obeying instructions hidden in a web page.

## The bet

**One engine, many agents, the same rules for all of them.**

1. **Declare, don't code.**
   - An agent is an `agent.yaml`: pattern, models by role, tools by exact ref, budgets,
     guardrails and memory.
   - The compiler turns any of the 18 patterns into one kind of plan. A control added once then
     applies to every agent.
2. **Controls are not optional, and not the model's job.** Each one is enforced by the
   platform, outside the model:
   - policy on every tool call;
   - approvals before irreversible actions;
   - untrusted content treated as data;
   - least-privilege identities;
   - secrets only from OpenBao;
   - budgets.

   The red team tests them against a model that has been fully fooled.
3. **Reuse, never fork.**
   - The platform runs on Project 0's shared services: the gateway, databases, identity,
     policy, secrets, observability and retrieval.
   - Other teams build on the platform through the SDK and template, in their own repositories.
4. **Evidence before claims.** A step is done only when its checks pass. A number is reported
   only with the run that measured it.
5. **Standards at the edges, so nothing is locked in:**
   - MCP for tools;
   - A2A for other agents;
   - AG-UI for interfaces;
   - OpenTelemetry GenAI conventions for traces;
   - LangGraph behind an adapter.

## What this buys

- **Speed for teams.** A new project starts from a working, tested agent. In CI, cold, its first
  run, eval and deployment check took 36.6 s of machine time.
- **One place to fix things.** A control or a fix lands once and reaches every agent.
- **Explainability.**
  - Any run can be rebuilt from the audit trail and its checkpoints.
  - Every version of every spec, prompt, tool and model is recorded.
- **Safe change.** New versions go through gates, a canary and automatic rollback.

## What it costs

- **Friction.** Approvals, registry sign-off, gates and named approvers slow a change down on
  purpose. A low-risk agent pays less of it than a high-risk one, but never none.
- **A platform to run.** Five services, Temporal, and Project 0's dependencies, per environment.
  Each project from the template also runs its own copy (ADR 0050).
- **A contract to keep.** The spec schema and the SDK are now public surfaces. Changes need
  versions and a deprecation path.

## Where it stands (2026-10-17)

- **Proven here, on the Project 0 stand-in:**
  - the mechanics of all 40 steps;
  - the controls (57 of 57 red-team attacks held in CI);
  - deployment on a kind cluster through Argo CD;
  - a new project from the template;
  - a measured disaster-recovery drill.
- **Not yet proven:**
  - anything against the real Project 0;
  - quality on real models;
  - whether multi-agent teams beat single agents on the same tasks.

  These are the owner's next actions (VERIFY-ON-MAC), in `PROGRESS.md`.

## How to grow it

In order, because each depends on the one before:

1. **Real-model baselines** for every app, and human labels to calibrate the judges. Until
   then, the gates catch broken mechanics, not worse answers.
2. **Run against the real Project 0** on the Mac's Kubernetes, then staging. Close the
   VERIFY-ON-MAC items step by step.
3. **The ADR 0051 comparison:** team against single agent. Keep the team only if it earns its
   cost.
4. **Production hardening the docs already name:**
   - admission-time signature checks;
   - mTLS between services;
   - Temporal worker versioning, so durable agents can have canaries too;
   - a second approver for prod.
5. **More teams on the reuse kit:**
   - a project image build and signing workflow;
   - `copier update` to newer platform pins;
   - a shared-platform path for agents made only of specs and MCP tools.

## Principles to keep

- The least autonomy that does the job.
- Anything that can't be undone waits for a person who isn't the requester.
- What the model reads from outside is data, never instructions.
- Every agent gets only the tools its spec grants.
- No number without the run that measured it.
- Change through Git, by pull request, with the evidence attached.
