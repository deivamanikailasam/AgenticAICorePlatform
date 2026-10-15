# Step 15 · Routing, chaining and hybrid

## 1. At a glance

| | |
|---|---|
| **Goal** | Send each request down the right path, check each step's output before the next step uses it, and mix fixed steps with agents in one flow |
| **Time** | About two days |
| **You end up with** | `router.llm`, `router.embedding`, `router.rules`; the `embedder` slot; validation gates; `pattern: prompt_chain` and `pattern: router`; a labelled routing set with a scorecard; approval evidence bound to the exact action; the invoice-triage reference app running end to end |
| **New words** | router, embedding, validation gate, prompt chain, labelled set, approval evidence |

## 2. The big idea

Most real work is not one big agent. It is a front desk that sends each request to the right
team (routing), a series of steps where each checks the last one's work (chaining with gates),
and a mix of fixed procedure and judgement (hybrid). Step 15 gives the platform all three, plus
the missing link that lets a person's approval actually unlock an irreversible action.

> *Analogy:* a council service centre. The receptionist reads your request and sends you to
> rates, parking or permits (router). Each counter stamps your form before passing it on (gate).
> Routine forms go straight through; odd cases go to a case officer who uses judgement (agent).
> Refunds need a supervisor's signature on that exact form, for that exact amount (approval
> evidence).

## 3. What we're doing

1. Three routers behind one slot: an LLM, an embedding classifier and rules.
2. An `embedder` slot so the embedding router gets vectors from Project 0's gateway.
3. Validation gates on LLM steps, with retries and a fall-back path.
4. Two patterns: `prompt_chain` and `router`.
5. A labelled set and a harness that measure routing accuracy.
6. Approval decisions bound to the exact action, accepted by policy.
7. The spec's invoice-triage example, completed and run end to end.

## 4. Why we're doing this

A wrong route wastes everything after it, and a bad intermediate answer poisons every later step.
Checking early is cheaper than discovering problems at the end. And until now an irreversible
action could never run: policy always required an approval, but the approval never reached
policy. The platform failed closed, which is safe but useless for a payment flow.

> *Watch out:* a router that is "usually right" still sends some customers to the wrong desk.
> Measure it on labelled examples, and give unsure decisions a fallback route.

## 5. Key ideas

- **LLM router.** The model picks from the declared routes only (the reply format allows nothing
  else), optionally helped by a one-line description per route. Flexible with odd wording; one
  model call per request.
- **Embedding router.** Each route has example requests. The request and the examples are turned
  into vectors (embeddings) by Project 0's embedding model; the route whose closest examples are
  most similar wins. No chat model per request, so it is cheap and steady once the examples are
  good.
- **Rules router.** Patterns per route, first match wins, otherwise a default. Free and fully
  predictable; only as good as the vocabulary you anticipated.
- **Confidence floor.** `min_confidence` with a `fallback` route sends unsure decisions to a
  human or a clarifying step instead of guessing.
- **Validation gate.** A check on an LLM step's output: a schema, simple checks (length, pattern,
  allowed values) or a named function. Failing output goes back to the model with the reasons,
  a bounded number of times; then the step fails and an `on_error` edge takes over.
- **Prompt chain.** A straight line of steps where every LLM step that feeds the next is gated.
- **Hybrid.** Workflow steps and agents in one graph: fixed steps where the path is known, an
  agent where judgement is needed.
- **Approval evidence.** A person's decision is attached to one exact action: the tool and a
  fingerprint of its arguments. The platform checks the fingerprint; policy checks the decision,
  that the approver is not the requester, and that they are in the right group.

## 6. How to do it

A router node with each kind of router:

```yaml
- id: triage
  type: router
  routes: [billing, technical, human]
  config:
    router: embedding                 # llm (default) | embedding | rules
    template: "$input.message"
    examples_file: routing/examples.jsonl   # {"text": ..., "route": ...} per line
    min_confidence: 0.6
    fallback: human
```

```yaml
config:
  router: rules
  template: "$input.message"
  default: technical
  rules:
    - { route: billing, any: ['\binvoice', 'refund'] }
```

A prompt chain with a gate:

```yaml
pattern: prompt_chain
graph:
  nodes:
    - id: extract
      type: llm
      config:
        template: "Extract the amount and currency from: $input.text"
        output_schema: { type: object, required: [amount, currency] }
        gate:
          checks: [ { path: currency, one_of: [AUD] } ]
          max_retries: 1
    - { id: draft, type: llm, config: { template: "Confirm ${extract.amount} ${extract.currency}." } }
    - { id: needs_human, type: function, fn: identity }
  edges: ["extract -> draft: on_ok", "extract -> needs_human: on_error"]
```

Measure routing accuracy, and run the reference app, on the Mac:

```bash
just eval-routing
just demo-invoice-triage INV-1002 j.smith
```

## 7. Benefits

- The cheapest router that is accurate enough can be chosen on evidence, per route set.
- Bad intermediate answers are caught where they happen, with a clear fall-back path.
- One graph mixes fixed steps and agents, so judgement is used only where it is needed.
- An approval can unlock exactly the action that was approved, and nothing else.

## 8. Drawbacks and risks

- Each gate retry and each LLM routing decision costs a model call.
- The embedding router needs representative examples; the offline test embedder only proves the
  plumbing.
- Gates check form, not truth: a well-formed wrong answer passes unless a check catches it.
- The approver's identity comes from whoever resumes the run until the run API (Step 17) takes
  it from a verified token. Routing approvals to groups, SLAs, edits, escalation, expiry and
  two-person approval are Step 28.
- No accuracy figures yet: they come from `just eval-routing` on the Mac.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| LLM router | Embedding router | High volume, stable topics, good examples; lower cost per request |
| LLM router | Rules router | Fixed vocabulary (form codes, product names); zero cost and full predictability |
| Gate on the step | Separate check step (function node) | The check needs other steps' results or external data |
| Prompt chain | Workflow (Step 12) | Steps branch or run in parallel |
| Router pattern | Supervisor (Step 22) | The handlers need to talk to each other or be re-delegated |

## 10. Which approach fits which situation

- **Support inbox, a few clear topics, lots of traffic:** embedding router with a fallback to a
  person.
- **Messy free-text requests, low volume:** LLM router with route descriptions.
- **Structured codes or keywords:** rules router.
- **Extract, transform, then write:** prompt chain with a gate after each LLM step.
- **Routine cases plus exceptions that need judgement:** hybrid, with an agent only on the
  exception path (as invoice-triage does).
- **Anything irreversible:** an approval in front of it; the evidence makes it run only as
  approved.

## 11. Common mistakes

- Training the embedding router on the same examples you test it on: keep `train.jsonl` and
  `test.jsonl` apart.
- No fallback route: an unsure router still has to pick something.
- An unconditional edge after a step that can fail: the next step runs after the error too; use
  `on_ok` (the chain builder refuses this).
- Naming a payment argument differently from the policy's limit (`amount` instead of
  `amount_aud`): the limit would not apply.
- Assuming one person can approve a high-risk irreversible action: two-person approval is not
  satisfiable yet, by design.

## 12. Done when

| Check | Status | Evidence (this build) |
|---|---|---|
| Prompt chains with validation gates | ✅ | a gate sends a bad currency back and the corrected value flows to the next step; a gate that keeps failing takes `on_error` after exactly 2 model calls; the builder refuses ungated, branching and leaky chains; both adapters |
| LLM router; embedding classifier or rules alternatives | ✅ | all three pass the router contract; each chosen from `agent.yaml` on both adapters; confidence floor sends unsure decisions to the fallback route |
| Hybrid graphs mixing workflow and agent nodes | ✅ | `pattern: router` sends a maths question to a ReAct agent with a calculator and a greeting to an LLM step; invoice-triage mixes functions, an agent, an approval and a tool |
| Routing accuracy measured on a labelled set | ✅ (harness and set) / ⚠️ **VERIFY-ON-MAC** (numbers) | `evals/labelled/routing`: 48 held-out cases, 24 training examples, 4 routes; the harness's arithmetic is tested on known predictions and every router runs all 48 cases; real accuracy for the LLM and embedding routers comes from `just eval-routing` with Project 0's models |
| The hybrid reference app runs end to end | ✅ (scripted model) / ⚠️ **VERIFY-ON-MAC** (real model) | invoice-triage on both adapters with the demo ERP over real MCP, rag-core search on behalf of the user, policy and audit on every call: valid invoice paid once after approval; invalid invoice resolved by the agent then paid; rejection pays nothing; self-approval refused by policy (12 tests). Real model: `just demo-invoice-triage` |
| ADR | ✅ | ADR 0026 |

## 13. Recap

Step 15 added the shapes most real flows need. Requests are routed by an LLM, an embedding
classifier or rules, each measurable on a labelled set and each with a safe fallback. Chain
steps are gated, so a bad answer is fixed or diverted before it spreads. Workflow steps and agents
mix freely in one graph. And a person's approval is now evidence bound to the exact action, so
irreversible steps can finally run, but only as approved. The spec's invoice-triage example runs
end to end on both runtimes.
