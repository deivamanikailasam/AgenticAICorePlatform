# Golden task sets for every reference app (715 tasks) and their harnesses

**Step:** 34 (in progress) · **Commit:** feat(evals): golden task sets for all seven reference apps · **Date:** 2026-10-16

## What changed
- **Scope (owner delegated the decision, 2026-10-16).** The reference apps are the 7 top-level specs, the same ones that have system cards (Step 32).
- **`agent_evals.generators`.** Seeded generators for six of them, at 100 tasks each. Every task is built as a known case:
  - **hello-workflow:** greet the given name exactly, including tricky inputs (an instruction, HTML, non-Latin text), with no model call.
  - **support-handoff:** 40 billing, 40 technical and 20 general messages. Triage must hand each to the right worker, or none for general questions.
  - **research-report:** two documents give a figure for two years. The report must state the computed percentage change, with search and calculator used and all three workers involved.
  - **review-chat:** a product blurb that mentions the product within a word limit, with writer and critic both involved.
  - **batch-summary:** 94 batches of 2–12 documents, needing one mapper call per document, one reducer call and every region named. Plus 6 batches over the 50-item limit, which must be refused before any work.
  - **policy-debate:** pro, con and judge all take part, and the answer states a decision.
- **invoice-triage** keeps its 115 tasks. `just eval-tasks` regenerates all seven sets.
- **`agent_api.eval_apps.SpecHarness`.** The harness for the six spec-only apps:
  - real built-in tools;
  - the rag-core search tool, whose HTTP API is answered from each task's own documents;
  - workers resolved next to the spec.

  Registered as `agent_platform.eval_apps` entry points.
- **`evals/rubrics/research-grounding.md`.** The judge rubric for reports.

## Why
Step 34: at least 100 golden tasks per reference app, with expected outcomes and deterministic checks first.

## How to check it
`uv run pytest services/agent-api/tests/test_eval_apps.py`, through the run API:
- every set is current and has at least 100 tasks;
- hello-workflow passes all 100 tasks with no model, which is the app's real result;
- with rule-based stand-in agents, routing, map-reduce counts, oversized-batch refusal, debate and review pass;
- a triage agent that misroutes billing questions to tech fails them with "delegated to tech";
- research tasks' documents are what the search tool returns.

## Risks and follow-ups
- The team tasks check structure and facts that are known in advance. Writing quality is for the judge rubrics, which need human calibration (owner, with the Mac's real-model outputs).
- The research task's expected percentage is rounded to one decimal place, and the check accepts that exact figure.
