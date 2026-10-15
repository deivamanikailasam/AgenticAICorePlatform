# ADR 0026: Routing, prompt chains with gates, an embedder slot, and approval evidence bound to the call

- **Status:** Accepted (amends ADR 0023, point 5)
- **Date:** 2026-10-15
- **Step:** 15
- **Deciders:** repository owner (delegated decisions to the build)

## Context
Step 15 asks for prompt chains with validation gates between steps, LLM or classifier routers
(LLM router → embedding classifier or rules), and hybrid graphs mixing workflow and agent nodes
as in the spec's `agent.yaml` example. Done when: routing accuracy is measured on a labelled set,
and the hybrid reference app runs end to end.

Two findings shaped the work:
- The reference app ends in an irreversible payment. Every policy engine requires approval for
  irreversible actions, and the approval node's decision never reached policy (ADR 0023 deferred
  that to Step 28). So no irreversible action could ever run, and the app could not finish.
- The spec loader rejected `${node.field}` in node templates, a form documented by the executors
  and the Step 13 and 14 chapters.

No new third-party dependency was needed (checked 2026-10-15). Embeddings come from Project 0's
LiteLLM gateway (`/v1/embeddings`), not from a library loaded in-process.

## Decision
1. **Routers are components on the `router` slot:** `llm` (default; structured output whose
   route is an enum of the declared routes, with optional descriptions), `embedding`
   (mean cosine similarity of the `k` closest labelled examples per route, softmax confidence,
   example vectors cached per router) and `rules` (ordered regular expressions, first match,
   default with confidence 0). A router node picks with `config.router` or `components.router`;
   `min_confidence` plus `fallback` sends unsure decisions down a fallback route.
2. **A new `embedder` slot (the 26th)** with a contract (deterministic, fixed length, similar
   texts closer), two offline hashing dummies for tests, and `embedder.gateway` under the
   `embedder` role with its own key and cost accounting.
3. **Validation gates on LLM steps** (`config.gate`): JSON Schema, declarative checks and an
   optional named function; failures go back to the model with the reasons, at most
   `max_retries` times, then the step fails (`GateFailedError`) so an `on_error` edge applies.
4. **`pattern: prompt_chain`**: a straight line of llm, function, tool and approval steps; every
   LLM step that feeds another must have a gate; branching only on `on_error`; a step with an
   `on_error` exit must use `on_ok` for its next edge (unconditional edges also run after errors).
   **`pattern: router`**: the entry is a router and every route has an edge; handlers may be
   agents. Both are graph patterns in the spec validator.
5. **Routing accuracy harness** (`agent_compiler.route_eval`, `just eval-routing`) over
   `evals/labelled/routing` (48 held-out cases, 24 training examples, 4 routes). The report
   always states dataset path and size, model alias per router and time. Numbers only from real
   runs.
6. **Approval evidence now, not at Step 28.** The approval node's decision becomes
   `ApprovalEvidence` bound to the exact call (tool name and SHA-256 of canonical-JSON
   arguments), for tool nodes and for agent-proposed calls. The platform verifies the binding
   (`bound`); the spec's approver group is checked by the executor; policy (OPA `2026-10-15.2`,
   Cerbos, in-process rules) accepts an approval only if approved, bound, by someone other than
   the requester and in the policy's approver group. Denials still win. Two-person approval
   cannot be satisfied by one decision. Step 28 adds routing, SLAs, edit-and-approve, escalation,
   expiry and two-person approval on top.
7. **Run-value references in templates.** The loader leaves lower-case dotted `${a.b}` for the
   executors; environment variables are upper case.
8. **Reference app** `apps/invoice-triage` (workspace package): validation and payment-details
   functions registered by name (`agent_platform.functions` entry points), a demo ERP MCP server
   with its own business rules, and the payment argument named `amount_aud` so the policy's
   payment limit applies.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Wait for Step 28 for approval evidence | The reference app could not finish; all irreversible actions stay impossible. Doing the minimal, bound version now loses nothing Step 28 needs |
| Policy recomputes the argument digest in Rego | JSON number formatting differs between Python and OPA (`480.0` vs `480`); the platform verifies the binding and passes a boolean fact |
| Let the spec's approver group satisfy policy | A spec author could name a group they belong to and loosen policy; the spec group is an extra check, never a replacement |
| In-process embedding model (sentence-transformers) | A second model path outside the gateway: no keys, budgets or cost accounting; reuses nothing from Project 0 |
| Gates as separate graph nodes | Retrying the model with the reasons needs the conversation; keeping the gate on the LLM step keeps the retry local |
| Coalescing syntax in tool arguments (`first_of`) | A new mini-language; an explicit `prepare_payment` function is clearer and testable |

## Consequences
The approver identity and groups come from the caller of `resume` until the run API (Step 17)
takes them from a verified token. Real routing accuracy and real-model behaviour of the
reference app are VERIFY-ON-MAC. The `local-embed` alias is an assumption until checked against
Project 0's LiteLLM configuration.

## Verification
`libs/agent-runtime/tests/test_routing.py`, `test_gates.py`, `test_embeddings.py`,
`test_executors.py`; `libs/agent-compiler/tests/test_routing_specs.py`,
`test_chain_and_router_patterns.py`, `test_route_eval.py`, `test_react_agent.py`;
`libs/agent-tools/tests/test_authorising.py`; `policies/agent/tools/decision_test.rego`
(28) and `policies/cerbos/tests/tool_test.yaml` (12); `apps/invoice-triage/tests` (12, both
adapters).
