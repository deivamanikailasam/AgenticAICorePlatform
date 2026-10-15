# ADR 0032: Context windows built per call, rolling summaries by default, untrusted content stays data

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 21
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 21 asks for a `ContextManager` with:
- token budgeting per section (system, tools, memory, history, retrieved data);
- history trimming and rolling summaries;
- tool-result compaction;
- just-in-time tool loading for large tool sets;
- prompt-cache-friendly ordering;
- clear delimiting of untrusted content.

Strategies (summarise, trim, offload to S3) must be selectable. Done when: a 200-turn
conversation stays under the context limit with no drop in task success on the long-dialogue
eval. OWASP ASI06 also covers context poisoning, for example through summaries.

The Step 2 contract `ContextManager.build(sections, budget)` is synchronous, but summaries and
offloading need I/O. No new packages were needed.

## Decision
1. **Build a window for every model call. Never edit the run's transcript.**
   - `ContextWindow.messages()` (async) compacts, summarises or archives.
   - `build()` (sync, the contract) fits the sections into the budget.
   - Checkpoints and audit keep the full transcript.
2. **Sections and budgets.**
   - System text and the current request are required.
   - Memory, summary, history, retrieved data and this turn's work share the rest by weight;
     unused share flows to the others.
   - The budget is three quarters of the model's window, minus the tool definitions and an
     answer reserve, unless `context.window_tokens` sets it.
3. **Atomic groups.** A tool call and its results are kept or dropped together.
4. **Compaction.** Long results from earlier rounds keep their beginning and end, plus a pointer
   to the full result (the executor's artefact URI, or an offloaded copy). The latest round is
   never cut.
5. **Strategies** (`context_manager` slot):
   - `rolling_summary` (default): the summary is extended chunk by chunk and capped at its
     budget share, which keeps the prompt prefix stable between updates.
   - `trim`.
   - `offload_s3`.

   Summarisers: `llm` (the `summariser` role, default) or `extractive` (no model, copies only
   what the user said).
6. **Untrusted content stays data.**
   - Untrusted messages are wrapped in `<untrusted>` tags.
   - A summary that drew on untrusted content is itself untrusted and wrapped.
   - The summariser prompt treats delimited text as data.
7. **Just-in-time tools.** Above `jit_tools_over` tools, the model sees the most relevant few,
   the ones already used, and `load_tools`. The loop answers `load_tools` itself, and only
   tools the agent may already use can be loaded. The first ranking is word overlap.
8. **Cache-friendly order:** system, memory, summary, history, request, work.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Make `build` async in the contract | Breaks Step 2 implementations. The async stage sits beside it instead |
| Each engine's own context handling | Different behaviour per engine, and no shared poisoning rules |
| Exact tokenizer (Qwen tokenizer files) | A heavy dependency tied to one model family. The estimate plus a margin is enough, and exact counting can arrive per model through the gateway |
| Embedding ranking for tools now | Word overlap works on the current tool sets. The `embedder` slot can replace it |
| Trim only | Loses early facts: 15/20 on the long-dialogue eval at 8,192 tokens, against 20/20 with rolling summaries |

## Consequences
- Delimiting changes what models see for untrusted content (text-protocol observations,
  recalled untrusted memories, untrusted summaries).
- `offload_s3` needs a way for the model to read the archive before it helps in chat. It suits
  tool-heavy runs.
- The OpenAI Agents SDK and Pydantic AI engines keep their own context handling.

## Verification
- `libs/agent-runtime/tests/test_context.py` (21 tests): the contract for the three strategies,
  atomic groups, compaction pointers, the stable prefix, untrusted summaries.
- `test_context_in_loop.py`: every call within budget over 25 tool rounds; just-in-time
  loading from 40 tools.
- `just eval-long-dialogue`: 5 dialogues, 1,000 user turns, 20 questions, stand-in model, at a
  window of 8,192 tokens.
  - rolling_summary: 20/20, largest prompt 4,162, 0 calls over the window.
  - unlimited baseline: 20/20, largest 12,329, 12 calls over.
  - trim: 15/20.
  - offload_s3: 11/20.
