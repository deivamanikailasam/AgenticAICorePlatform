# Step 3 · The `agent.yaml` spec

## 1. At a glance

| | |
|---|---|
| **Goal** | One reviewable file that fully describes an agentic system, checked before it runs |
| **Time** | About a day |
| **You end up with** | `libs/agent-spec` (schema, loader, semantic checks, migrations, CLI), two example specs, `just validate`, the generated JSON Schema and reference page |
| **New words** | schema, interpolation, semantic validation, migration, secret reference |

## 2. The big idea

An `agent.yaml` is like a building-permit application. It says what you want to build (the
pattern), who is responsible (owner), how risky it is (risk tier), which tools and powers it
needs, the limits it must respect (budgets) and how it will be inspected (evals). The clerk
(the loader) checks the form is complete and consistent and hands back *every* problem at once,
with the line it is on.

> *Analogy:* a recipe card that the head chef checks before service: every ingredient listed,
> every step in an order that ends, and a cooking-time limit on every dish.

## 3. What we're doing

1. Model the whole spec in Pydantic (`libs/agent-spec/src/agent_spec/model.py`).
2. Load YAML safely, migrate old versions, interpolate environment variables.
3. Keep secret references unresolved and only allow them in tool configuration.
4. Run semantic checks across the parts of the spec.
5. Report all problems with line numbers; `agent-spec validate` / `just validate`.
6. Generate the JSON Schema and reference page into `docs/reference/`.

## 4. Why we're doing this

The cheapest bug is the one caught before anything runs. A spec that references a tool it did
not declare, loops forever, or calls models without a cost limit should never reach the runtime.
For governance, one file holds every permission an agent has, so reviewers, auditors and the
policy engine can all read the same thing.

> *Why this matters:* the spec is where "least autonomy", "budgets on by default" and "secrets
> never in prompts" become enforceable rules rather than good intentions.

## 5. Key ideas

- **Schema validation** checks shape and types (is `max_steps` a positive number?).
- **Semantic validation** checks meaning across fields (does the tool a node uses exist in
  `tools`? does every cycle have a bound?).
- **Interpolation** fills `${VAR}` from the environment so the same spec works in dev and prod.
- **Secret reference** `${secret:erp/api#token}` names a secret without containing it. The tool
  fetches the value from OpenBao at call time.
- **Migration** upgrades an older `spec_version` automatically, one version at a time.
- **Model role** (`planner`, `executor`) names *what a model is for*; `models:` maps roles to
  gateway aliases.

## 6. How to do it

```bash
just validate                                       # every spec under agents/
uv run agent-spec validate path/to/agent.yaml       # one file
uv run agent-spec validate --strict agents/         # warnings fail too
just spec-reference                                 # regenerate docs/reference/
```

A complete example is `agents/invoice-triage/agent.yaml`. The main sections:

| Section | Purpose |
|---|---|
| `spec_version`, `name`, `version`, `owner`, `description` | Identity and ownership |
| `risk_tier` (`low`/`medium`/`high`) | Drives approvals, eval gates and monitoring |
| `autonomy`, `pattern` | How much the model decides, and which pattern runs |
| `models` | Role → gateway alias (`default` is required) |
| `runtime` | Adapter (`langgraph`, …) and durability (`memory`/`checkpoint`/`temporal`) |
| `budgets` | Steps, wall time, tokens, AUD cost, tool calls, delegation depth, fan-out |
| `tools` | `mcp://`, `local://`, `a2a://`, `sandbox://` refs, approvals, rate limits, config |
| `memory`, `guardrails` | Short/long-term memory; checks before and after calls |
| `graph` | `entry`, `nodes` (function, llm, tool, agent, router, approval, subgraph, join), `edges` |
| `agents`, `components`, `evals`, `interop`, `labels` | Workers, component choices, eval suite, publishing, tags |

What a failure looks like:

```text
FAIL libs/agent-spec/tests/fixtures/invalid/semantic.yaml
     error: …/semantic.yaml:19: graph.nodes[1]: tool 'send_money' is not declared in tools (get_invoice)
     error: …/semantic.yaml:22: graph.edges: cycle b -> c -> b has no loop bound; set max_iterations on one of its nodes
     error: …/semantic.yaml:8: budgets.max_tokens: required when the spec calls models
```

## 7. Benefits

- Mistakes surface at review time, all at once, with line numbers.
- One file for reviewers, policy and auditors; diffs show exactly what permissions changed.
- Editors can autocomplete from the generated JSON Schema.
- Old specs keep working through migrations.

## 8. Drawbacks and risks

- YAML is easy to mis-indent; the loader reports the line, but reviewers still need care.
- The schema is a public contract; changing it needs a new version and a migration.
- Some checks (does the MCP server really offer this tool?) need live systems and happen later
  (Steps 6–8).

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| YAML + Pydantic | Python-only definitions | Prototypes owned by one engineer |
| Generated JSON Schema | Hand-written schema | Never; it drifts |
| Unresolved secret refs | Env vars with secrets | Never for production: values leak into plans and traces |
| Required budgets | Optional budgets | Never; budgets are a safety default |

## 10. Which approach fits which situation

- **Deterministic process:** `autonomy: workflow`, `pattern: workflow`, a graph of function and
  tool nodes.
- **One tool-using assistant:** `autonomy: agent`, `pattern: react`, tools listed, budgets with
  tokens and cost.
- **Mixed process with an agentic step:** `autonomy: hybrid`, `pattern: hybrid`, an `agent` node
  inside the graph (like invoice-triage).
- **Teams of agents:** `autonomy: multi_agent`, `pattern: supervisor` (Phase E), `agents:` list.

## 11. Common mistakes

- Putting a secret value (or a secret reference) in `description` or a prompt: refused.
- Naming a raw model id on a node instead of a role from `models:`.
- A review loop (`draft -> review -> draft`) without `max_iterations`.
- Forgetting `max_tokens` and `max_cost_aud` on a spec with agent or LLM nodes.

## 12. Done when

| Check | Status | Evidence |
|---|---|---|
| Invalid specs fail with readable errors | ✅ | `test_all_schema_problems_reported_with_lines`, `test_semantic_problems`; CLI output above |
| `just validate agents/` passes | ✅ | `validate: 2 file(s), 0 failed` |
| Schema published in `docs/reference/` | ✅ | `agent-spec.schema.json`, `agent-yaml.md`, checked by `spec-reference-check` |
| `${env}` and secret references | ✅ | `test_env_interpolation_and_missing_variables`; secret placement rule in `test_semantic_problems` |
| `spec_version` and migrations | ✅ | `test_v0_layout_is_migrated`, `test_future_spec_version_rejected` |
| ADR | ✅ | ADR 0013 |

## 13. Recap

Step 3 made `agent.yaml` a checked contract: a Pydantic schema for every section, a loader that
interpolates environment variables but never resolves secrets, semantic checks for the mistakes
a schema can't see, automatic migrations between spec versions, all-at-once errors with line
numbers, `just validate` in every check, and a generated JSON Schema and reference page.
