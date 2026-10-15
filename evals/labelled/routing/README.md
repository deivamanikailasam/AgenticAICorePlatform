# Routing labelled set

Support requests labelled with one of four routes: `billing`, `technical`, `sales`, `account`.

| File | Contents |
|---|---|
| `train.jsonl` | 24 examples (6 per route). The embedding router learns from these only. |
| `test.jsonl` | 48 held-out cases (12 per route), never shown to any router. |
| `rules.yaml` | The rules router's patterns and default route. |

Written for this build (synthetic, Australian English) and deliberately mixed: some cases
carry obvious keywords, others say the same thing indirectly ("money left my account", "white
screen in Safari"), and a few sit near a boundary (SSO as a priced feature is `sales`; SSO for
my own login is `account`). The rules were written by the same person as the cases, so their
score on this set is optimistic.

Run all three routers against Project 0's gateway (on the Mac):

```bash
just eval-routing
```

The report states the dataset size, the model alias behind each router and the time of the
run. Quote numbers only from such a run, with those details. 48 cases is a small set: one case
is about 2 percentage points.
