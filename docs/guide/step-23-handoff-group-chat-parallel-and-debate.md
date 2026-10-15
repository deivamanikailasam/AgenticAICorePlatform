# Step 23 · Handoff, group chat, parallel and debate

## 1. At a glance

| | |
|---|---|
| **Goal** | Four more ways for agents to work together, each guaranteed to stop |
| **Time** | About two days |
| **You end up with** | `pattern: handoff`, `group_chat`, `map_reduce`, `debate` with a `team:` section; reference specs `support-handoff`, `review-chat`, `batch-summary`, `policy-debate`; `just eval-teams` with limit checks |
| **New words** | handoff (swarm), group chat, map-reduce, debate, termination condition |

## 2. The big idea

Supervisors (Step 22) are one way to organise a team. Others fit other jobs:
- **handoff:** pass the customer to the right specialist;
- **group chat:** let a writer and a critic go back and forth;
- **map-reduce:** split a big batch into many small jobs that run side by side;
- **debate:** have agents argue while a judge decides.

Every one of them has two ways to end: its natural finish, and hard limits on turns, items and
money that agents cannot talk their way past.

> *Analogy:* meetings with a chair and a clock. A good meeting ends when the decision is made;
> every meeting ends when the room booking runs out.

## 3. What we're doing

1. Add a `PatternBuilder` for each pattern, all building the same team node.
2. Reuse Step 22's team runtime: own tools per agent, policy on every hand-over, circuit breakers,
   one budget for the team.
3. Give each pattern its own end condition and hard limits.
4. Write a reference spec for each pattern.
5. Run an eval that includes agents that never stop on their own.

## 4. Why we're doing this

Multi-agent systems fail in a recognisable way: two agents handing a case back and forth, a
critic that is never satisfied, a judge that never decides, a fan-out over far too many items.
Each burns money until someone notices (OWASP ASI08, cascading failures). The platform has to
stop them by design.

> *Watch out:* "the agents will agree eventually" is not a termination condition. Every pattern
> here has a limit that does not depend on what the agents say.

## 5. Key ideas

- **Handoff.** Start at `entry`. An agent either answers, or hands off with a note to an agent
  it is allowed to hand to (`handoffs`). Handoffs off the listed edges stop the run.
- **Group chat.** Speakers take turns (round robin, or a selector model choosing only among the
  team). It ends when a message contains `stop_phrase`, and the answer is the latest message of
  `answer_from`.
- **Map-reduce.** The mapper runs per item, at most `concurrency` at a time. Inputs over
  `max_items` are refused before anything starts. The reducer combines the results.
- **Debate.** Debaters argue in rounds. The judge may end it after any round, and must answer
  after the last.
- **Hard limits everywhere.** `max_turns`, `max_items`, the team budget across all agents,
  circuit breakers and nesting depth. Ending without an answer means `stopped`, never
  `succeeded`.

## 6. How to do it

```yaml
autonomy: multi_agent
pattern: handoff                  # group_chat | map_reduce | debate
agents:
  - { name: triage, spec: workers/triage.yaml }
  - { name: billing, spec: workers/billing.yaml }
  - { name: tech, spec: workers/tech.yaml }
team:
  entry: triage
  handoffs: { triage: [billing, tech], billing: [tech], tech: [billing] }
  max_turns: 4
```

Other patterns use `speaker`, `stop_phrase`, `answer_from` (group chat); `items`, `mapper`,
`reducer`, `concurrency`, `max_items` (map-reduce); `debaters`, `judge` (debate). Then run the
eval:

```bash
just eval-teams
```

## 7. Benefits

- The right pattern for each job, with the same safety rules for all of them.
- Runaway conversations, ping-pong and over-large batches stop by design.
- Parallel work finishes faster without exceeding a concurrency cap.
- One budget covers the whole team, whichever pattern it uses.

## 8. Drawbacks and risks

- More agents means more model calls. Use the simplest pattern that works.
- Map-reduce can overshoot the budget by the mappers already running.
- Handoff notes reach the next agent as input. Agents must treat them as data.
- The offline eval uses rule-based agents: it proves limits and routing, not collaboration
  quality.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Handoff | Supervisor (Step 22) | A coordinator should see the whole task and decide each step |
| Group chat | Reflection in one agent (Step 14) | One agent can critique itself well enough |
| Map-reduce | A loop in a workflow | Few items, or each depends on the previous |
| Debate | A single judge-model check | The answer needs checking rather than arguing |

## 10. Which approach fits which situation

- **Customer support routing:** handoff with explicit allowed edges.
- **Drafting with review:** a group chat of writer and critic, with `answer_from: writer`.
- **Summarising a batch of documents:** map-reduce, with `concurrency` matched to the gateway's
  rate limits.
- **Policy or design decisions:** a debate with a judge, and few rounds.

## 11. Common mistakes

- Allowing every agent to hand off to every other agent, which invites ping-pong.
- Taking the critic's "approved" as the result of a chat.
- Feeding an unbounded list to map-reduce.
- No `max_turns` because "the judge will decide".

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| Each pattern has a reference spec and eval | ✅ | `support-handoff`, `review-chat`, `batch-summary`, `policy-debate`; `just eval-teams`, 7 golden tasks all correct (stand-in agents) |
| No pattern can run past its budget or termination condition | ✅ | 8 limit checks pass. Handoff ping-pong stops at `max_turns` (4 calls); a never-approving critic stops at 6; 60 items over `max_items` 50 are refused with 0 calls; a judge that never decides stops after 3 rounds (9 calls). With an AUD 0.002 budget, chat and debate stop exactly there, map-reduce one in-flight wave over (AUD 0.004), handoff finishes inside it |
| Handoff, group chat (speaker selection), map-reduce (concurrency limit), debate (judge) | ✅ | tests: disallowed handoff stops; policy denial inside a pattern; at most 3 mappers at a time with concurrency 3; the selector chooses only team members and sees messages as data |
| Each pattern is a PatternBuilder | ✅ | `pattern_builder.handoff`, `.group_chat`, `.map_reduce`, `.debate` |
| ADR | ✅ | ADR 0034 |

## 13. Recap

Step 23 added handoff, group chat, map-reduce and debate on the team runtime from Step 22, so
every pattern keeps per-agent tools, policy on hand-overs and one team budget. Each has its own
natural end and hard limits that agents cannot bypass, and the eval proves it by running agents
that never stop on their own.
