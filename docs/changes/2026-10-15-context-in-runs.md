# Every agent model call goes through the context window; large tool sets load just in time

**Step:** 21 · **Commit:** feat(agent-runtime): context windows and just-in-time tools in agent runs · **Date:** 2026-10-15

## What changed
- **New `context:` section in `agent.yaml`.** Fields: `strategy`, `summariser` (`llm` or
  `extractive`), `window_tokens`, `compact_tool_results_over`, `jit_tools_over`, `jit_top_k`.
  The defaults turn on rolling summaries with three quarters of the model's window.
- **The native ReAct loop**, including the sub-loops of the planning and reflection
  strategies, builds each model call through a `ContextWindow`:
  - the budget is the window minus the tool definitions and a reserve for the answer;
  - the messages are compacted, summarised or trimmed to fit;
  - state is kept per transcript.

  The run's own transcript is never changed. Only what the model sees is.
- **Just-in-time tools.** Above `jit_tools_over` tools, the model sees the `jit_top_k` most
  relevant ones, the ones already used in the run, and `load_tools`. A `load_tools` search is
  answered by the loop itself and adds the matches from the next step. Only tools the agent may
  already use can be loaded.
- **Delimiting.** Untrusted observations in the text ReAct protocol now reach the model wrapped
  in `<untrusted>` tags.
- `executors_for` gains `artefacts`, needed for the `offload_s3` strategy.

## Why
Step 21: no model call may exceed the window, however long the run or large the tool set.
Showing 40 tool definitions on every call wastes context, and makes the model's choice worse.

## How to check it
`uv run pytest libs/agent-runtime/tests/test_context_in_loop.py`
- In a 25-round tool run, every call stays within budget, and the latest result is shown whole.
- With 40 tools, the first call shows 6 definitions; a `load_tools` search brings in the
  currency tool, which is then called.

## Risks and follow-ups
- Relevance ranking for just-in-time tools uses word overlap. An embedding ranker can replace it
  through the `embedder` slot.
- The text protocol still lists every tool.
- The OpenAI Agents SDK and Pydantic AI engines manage their own context.
