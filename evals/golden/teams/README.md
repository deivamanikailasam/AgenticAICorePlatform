# Team patterns set

| File | Contents |
|---|---|
| `cases.jsonl` | 7 tasks across the four Step 23 reference specs (`support-handoff`, `review-chat`, `batch-summary`, `policy-debate`), each with the text a correct answer contains (`expect`) |

The eval (`just eval-teams`) also runs adversarial versions of each pattern, where agents never
end on their own: handoff ping-pong, a critic that never says TERMINATE, more documents than
`max_items`, a judge that never decides, and a budget far too small. Each must stop at its
limit without claiming success.

Offline, every agent is a rule-based stand-in (see `agent_compiler.team_eval`), so results show
that the patterns route, end and stay in budget, not how well real models collaborate.
Synthetic, written for this build.
