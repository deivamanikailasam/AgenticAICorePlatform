# Long-dialogue set

| File | Contents |
|---|---|
| `dialogues.jsonl` | 5 dialogues of 200 user turns each. Small talk on a topic, four facts stated along the way (one is later corrected), and four questions at later turns (20 probes in all) |

The eval expands each line into a full conversation: filler user turns about the topic, a
canned assistant reply after each, the facts at their turns and the questions (`ask`) at theirs.
An answer counts as correct when it contains `expect`. Corrections test that the newest value
wins.

Offline, the stand-in model answers each question with the newest sentence in its context that
contains the question's cue word, so the result measures what the context strategy keeps
(history, summary), not how well a model reads it. `--model gateway` uses the real model and
summariser (Mac, VERIFY-ON-MAC).

Written for this build (synthetic). 20 probes: one probe is 5 percentage points.

```bash
just eval-long-dialogue                    # offline, all strategies against the unlimited baseline
just eval-long-dialogue --window 8000      # a different model window
```
