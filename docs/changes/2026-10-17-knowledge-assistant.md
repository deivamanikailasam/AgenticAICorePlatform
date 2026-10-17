# Reference app 1: the knowledge assistant (agentic RAG on rag-core)

## What changed
- `agents/knowledge-assistant/agent.yaml` (`pattern: agentic_rag`):
  - plans retrieval hops;
  - searches Project 0's rag-core through `mcp://rag-core/search` on the user's behalf;
  - answers with citations, checks the answer against them, and searches again when it falls
    short.
  - Low risk, published over A2A.
- 100 golden tasks (`evals/apps/knowledge-assistant/tasks.jsonl`), generated and seeded. Each
  task has three documents:
  - a policy naming who approves an expense above a limit (a role);
  - the asker's department's organisation chart;
  - another department's chart, as a distractor.

  The answer must name the right person and not the distractor, and retrieval must have used
  search.
- An eval harness (`knowledge-assistant`), the mechanics baseline, and the agent and system
  cards. CODEOWNERS, the register, lineage and dev's specs are regenerated.
- Two tests no longer require every dev agent to be in prod already. They now check that prod
  and staging release only agents dev has, since a new agent reaches prod through its promotion
  pull requests. The card test counts 21 specs.

## Why
Step 40's first reference app. The pattern has existed since Step 16, but no app used it.

## How to check it
- `uv run agent-spec validate --strict agents/knowledge-assistant`.
- `just eval-tasks --check`.
- `just eval-baseline-check knowledge-assistant`.

## Risks and follow-ups
- The mechanics baseline is 0/100 on the stand-in: its canned model answers give no retrieval
  plan, so it checks the harness and the platform's deterministic parts only. Step 16's
  scripted-model tests cover the pattern's mechanics.
- Quality needs a run on real models with rag-core (`--provider gateway`, VERIFY-ON-MAC).
- Promotion to staging and prod follows in separate commits.
