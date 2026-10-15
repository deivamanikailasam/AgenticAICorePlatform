# Step 21 · Context engineering

## 1. At a glance

| | |
|---|---|
| **Goal** | Every model call fits the model's context window, however long the conversation or large the tool set, without losing what matters or letting old tool output turn into instructions |
| **Time** | About two days |
| **You end up with** | `agent_runtime.context`: per-section budgets, atomic tool groups, tool-result compaction, `rolling_summary` (default), `trim` and `offload_s3` strategies, LLM and extractive summarisers, just-in-time tools; a `context:` section in `agent.yaml`; `just eval-long-dialogue` |
| **New words** | context window, rolling summary, tool-result compaction, just-in-time tools, prompt caching |

## 2. The big idea

A model can only read so much at once. A long chat or a tool-heavy run soon has more history
than fits. Instead of failing, or silently losing the start, the platform decides each time
what the model sees:
- the instructions and the current request, always;
- a short summary of the old parts;
- the recent conversation in full;
- shortened old tool results, with a pointer to the full text;
- only the tools that matter right now.

> *Analogy:* a briefing folder for a meeting. The agenda and today's question go on top, a
> one-page summary of past meetings comes next, then the last few emails in full. Long reports
> are reduced to their first and last page with "full copy in the archive". Nobody hands the
> chair the whole filing cabinet.

## 3. What we're doing

1. Split each transcript into sections and give each a share of the budget.
2. Keep tool calls and their results together.
3. Shorten old tool results.
4. Fold old turns into a rolling summary, or drop or archive them, per the strategy.
5. Show only the relevant tools when there are many, and let the model load more.
6. Order everything so provider prompt caches can reuse the start.
7. Keep untrusted content marked as data, including in summaries.
8. Prove it on 200-turn dialogues.

## 4. Why we're doing this

Overflowing the window either fails the call, or (with some providers) silently drops text.
Sending everything is slow and expensive. And a careless summary is a laundering machine: a
document's "ignore previous instructions" goes in as untrusted tool output and could come out
as a trusted-looking summary line (OWASP ASI06, context side).

> *Watch out:* a summary is only as trustworthy as what it summarised. Here, a summary of
> anything untrusted is itself untrusted and wrapped as data.

## 5. Key ideas

- **Sections.** System text and the current request are always kept. Memory, summary, history
  and the current turn's work share the rest by weight; unused share goes to the others.
- **Atomic groups.** A tool call never appears without its result, or a result without its
  call.
- **Compaction.** Old long tool results keep their beginning and end, plus a note with where
  the full result is. The latest round is never cut.
- **Strategies.**
  - `rolling_summary`: the oldest turns are folded into a summary, chunk by chunk, capped at
    its budget share.
  - `trim`: the oldest turns are dropped.
  - `offload_s3`: they are archived with a pointer.
- **Summarisers.** `llm` (the summariser model) or `extractive` (no model; keeps only what the
  user stated, so it cannot copy a document's instructions).
- **Just-in-time tools.** Above 20 tools, the model sees the most relevant 8, those already
  used, and `load_tools` to search for more. All three numbers are configurable.
- **Cache-friendly order.** System, memory, summary, history, request, work. Between summary
  updates the prompt's start is identical from call to call.

## 6. How to do it

Defaults are on for every agent node. Tune them in `agent.yaml`:

```yaml
context:
  strategy: rolling_summary        # trim | offload_s3
  summariser: llm                  # extractive: no model calls
  window_tokens: 24000             # default: 3/4 of the model's window
  compact_tool_results_over: 1500  # tokens
  jit_tools_over: 20
  jit_top_k: 8
```

Run the eval:

```bash
just eval-long-dialogue                    # offline, window 8192
just eval-long-dialogue --window 16384
just eval-long-dialogue --model gateway    # Mac: real model and LLM summaries
```

## 7. Benefits

- No call overflows the window: 0 of 20 on the eval, against 12 of 20 for "send everything".
- Long conversations keep their facts (20/20 with rolling summaries) at about a third of the
  baseline's largest prompt.
- Large tool sets cost a few definitions per call instead of all of them.
- A stable prompt start lets provider caches cut cost and latency.
- Untrusted text stays marked as data, even after summarising.

## 8. Drawbacks and risks

- Token counts are estimates (about 4 characters per token). Budgets keep a margin.
- An LLM summary can still drop or blur a detail. Measure with the real model (VERIFY-ON-MAC).
- `offload_s3` helps tool-heavy runs more than chat, because the model cannot read the archive
  yet.
- Relevance ranking for tools uses word overlap.
- Framework engines (OpenAI Agents SDK, Pydantic AI) keep their own context handling.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| `rolling_summary` | `trim` | Short sessions, where losing the oldest turns does not matter and no summariser cost is wanted |
| `rolling_summary` | `offload_s3` | Tool-heavy runs with large results that must stay recoverable |
| LLM summariser | Extractive | No model budget, or the strictest poisoning stance |
| Just-in-time tools | All tools every call | Fewer than about 20 tools |

## 10. Which approach fits which situation

- **Customer chat that runs for hours:** `rolling_summary` with the LLM summariser. Add Step 20
  memory for facts that must outlive the session.
- **Research agent reading many documents:** `rolling_summary` plus compaction. Raise
  `compact_tool_results_over` if documents are short.
- **Ops agent with 100+ tools:** just-in-time tools, with a lower `jit_top_k` if the model gets
  confused.
- **Regulated, strict anti-poisoning:** the `extractive` summariser.

## 11. Common mistakes

- Setting `window_tokens` to the full model window: the tools and the answer need room too.
- Dropping a tool result but keeping its call: providers reject that, which is why groups are
  atomic.
- Summarising untrusted content into a "trusted" summary.
- Expecting `offload_s3` to help in chat without a way for the model to fetch the archive.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| A 200-turn conversation stays under the context limit with no drop in task success on the long-dialogue eval | ✅ | `just eval-long-dialogue`: 5 dialogues × 200 user turns, 20 questions, window 8,192 tokens, stand-in model. Rolling summary: 20/20 correct, largest prompt 4,162 tokens, 0 calls over the window. Unlimited baseline: 20/20 but 12,329 tokens, 12 calls over. Trim: 15/20. Offload: 11/20. Real model and LLM summaries: VERIFY-ON-MAC |
| Token budgets per section, history trimming, rolling summaries | ✅ | context contract for three strategies; budget tests |
| Tool-result compaction | ✅ | old results cut with a pointer; latest round whole |
| Just-in-time tool loading | ✅ | 40 tools: 6 definitions shown; `load_tools` brings in the one needed, which is then called |
| Prompt-cache-friendly ordering | ✅ | consecutive calls share an identical prefix between summary updates |
| Delimiting untrusted content (and ASI06 context poisoning) | ✅ | untrusted messages wrapped; a summary of a poisoned tool result is untrusted and wrapped; the extractive summary never copies document text |
| ADR | ✅ | ADR 0032 |

## 13. Recap

Step 21 put a briefing step in front of every model call. The platform budgets each part of the
context, keeps tool calls whole, shortens old results, and folds old turns into a rolling
summary that stays within its share. It shows only the tools that matter, orders the prompt so
caches can reuse it, and keeps anything untrusted marked as data, summaries included. On
200-turn dialogues every call fits the window, with no loss of task success against sending
everything.
