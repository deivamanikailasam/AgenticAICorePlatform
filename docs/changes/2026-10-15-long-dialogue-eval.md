# A 200-turn long-dialogue eval for the context strategies

**Step:** 21 · **Commit:** feat(agent-runtime): long-dialogue eval for context strategies · **Date:** 2026-10-15

## What changed
- New golden set `evals/golden/long-dialogue`: 5 dialogues of 200 user turns, with facts,
  corrections and 20 questions asked at later turns.
- `python -m agent_runtime.dialogue_eval` (`just eval-long-dialogue`) replays each dialogue
  four ways: the whole transcript every time (baseline), `rolling_summary`, `trim` and
  `offload_s3`. It reports answers correct, the largest prompt and calls over the window.
- Fix found by the eval: the extractive summariser missed first-person statements with "am",
  "have" and similar verbs ("I am allergic to penicillin."). Its pattern now covers them.

## Why
Step 21 Done-when: "a 200-turn conversation stays under the context limit with no drop in task
success on the long-dialogue eval".

## How to check it
`just eval-long-dialogue`. Runs in this build, 20 questions, stand-in model and extractive
summaries:

| Window | Strategy | Correct | Largest prompt | Calls over the window |
|---|---|---|---|---|
| 8,192 | unlimited | 20/20 | 12,329 | 12 |
| 8,192 | rolling_summary | 20/20 | 4,162 | 0 |
| 8,192 | trim | 15/20 | 5,120 | 0 |
| 8,192 | offload_s3 | 11/20 | 4,572 | 0 |
| 16,384 | unlimited | 20/20 | 12,329 | 0 |
| 16,384 | rolling_summary | 20/20 | 9,883 | 0 |
| 16,384 | trim | 20/20 | 11,264 | 0 |
| 16,384 | offload_s3 | 17/20 | 9,883 | 0 |

## Risks and follow-ups
- These numbers measure what each strategy keeps, using a stand-in reader. The real model with
  the LLM summariser is `--model gateway` on the Mac (VERIFY-ON-MAC).
- Token counts are estimates.
- `offload_s3` loses facts in chat because the model cannot read the archive. It suits
  tool-heavy runs, whose results the executor can fetch again.
