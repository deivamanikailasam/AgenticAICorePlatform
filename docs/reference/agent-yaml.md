# `agent.yaml` reference (spec_version 1)

Generated from the Pydantic models in `libs/agent-spec` by `just spec-reference` — do not
edit by hand. The machine-readable schema is `agent-spec.schema.json` next to this file.

Interpolation: `${VAR}`, `${VAR:-default}` and `${env:VAR}` are replaced when the spec is
loaded. `${secret:<path>#<key>}` is **never** resolved at load time and is only allowed
under `tools[].config`; it is fetched from OpenBao when the tool is called.

Edges may be written as `a -> b`, `a -> b: on_error`, or `{from: a, to: b, when: on_ok}`.

Patterns: `workflow`, `state_machine`, `prompt_chain`, `router`, `react`, `plan_execute`, `rewoo`, `reflection`, `evaluator_optimiser`, `agentic_rag`, `supervisor`, `hierarchical`, `handoff`, `group_chat`, `map_reduce`, `debate`, `hybrid`

### `AgentSpec (root)`

The root of ``agent.yaml``.

| Field | Type | Required | Default |
|---|---|---|---|
| `spec_version` | `1` |  | `1` |
| `name` | string | yes |  |
| `version` | string | yes |  |
| `owner` | string | yes |  |
| `description` | string |  |  |
| `risk_tier` | RiskTier | yes |  |
| `autonomy` | Autonomy | yes |  |
| `pattern` | Pattern | yes |  |
| `models` | object |  |  |
| `runtime` | Runtime |  | `{'adapter': 'langgraph', 'durability': 'checkpoint'}` |
| `budgets` | Budgets | yes |  |
| `tools` | list of ToolRef |  | `[]` |
| `memory` | Memory |  | `{'short_term': 'checkpoint', 'long_term': None}` |
| `guardrails` | list of GuardrailRef |  | `[]` |
| `graph` | Graph or null |  |  |
| `agents` | list of SubAgent |  | `[]` |
| `components` | object |  |  |
| `evals` | Evals or null |  |  |
| `interop` | Interop |  | `{'a2a_publish': False, 'mcp_publish': False}` |
| `labels` | object |  |  |

### `Approval`

| Field | Type | Required | Default |
|---|---|---|---|
| `required` | boolean |  | `True` |
| `approvers` | string |  | `group:approvers` |
| `two_person` | boolean |  | `False` |
| `sla_minutes` | integer or null |  |  |

### `Autonomy`

One of: `workflow`, `agent`, `multi_agent`, `hybrid`

### `Budgets`

Limits for one run. Safe by default: steps and wall-clock time are always required.

| Field | Type | Required | Default |
|---|---|---|---|
| `max_steps` | integer | yes |  |
| `max_wall_seconds` | number | yes |  |
| `max_tokens` | integer or null |  |  |
| `max_cost_aud` | number or null |  |  |
| `max_tool_calls` | integer or null |  |  |
| `max_delegation_depth` | integer |  | `2` |
| `max_fanout` | integer |  | `8` |

### `Durability`

One of: `memory`, `checkpoint`, `temporal`

### `Edge`

| Field | Type | Required | Default |
|---|---|---|---|
| `from` | string | yes |  |
| `to` | string | yes |  |
| `when` | string or null |  |  |

### `Evals`

| Field | Type | Required | Default |
|---|---|---|---|
| `suite` | string | yes |  |
| `gates` | string |  | `default` |

### `Graph`

| Field | Type | Required | Default |
|---|---|---|---|
| `entry` | string or null |  |  |
| `nodes` | list of Node | yes |  |
| `edges` | list of Edge |  | `[]` |

### `GuardrailRef`

| Field | Type | Required | Default |
|---|---|---|---|
| `name` | string | yes |  |
| `config` | object |  |  |

### `Interop`

| Field | Type | Required | Default |
|---|---|---|---|
| `a2a_publish` | boolean |  | `False` |
| `mcp_publish` | boolean |  | `False` |

### `LongTermMemory`

| Field | Type | Required | Default |
|---|---|---|---|
| `store` | string |  | `pgvector` |
| `scope` | `user` \| `tenant` \| `agent` |  | `user` |
| `policy` | string |  | `validating` |
| `ttl_days` | integer or null |  |  |

### `Memory`

| Field | Type | Required | Default |
|---|---|---|---|
| `short_term` | `checkpoint` \| `none` |  | `checkpoint` |
| `long_term` | LongTermMemory or null |  |  |

### `Node`

| Field | Type | Required | Default |
|---|---|---|---|
| `id` | string | yes |  |
| `type` | NodeType | yes |  |
| `fn` | string or null |  |  |
| `tool` | string or null |  |  |
| `tools` | list of string |  | `[]` |
| `pattern` | Pattern or null |  |  |
| `model` | string or null |  |  |
| `prompt` | string or null |  |  |
| `routes` | list of string |  | `[]` |
| `spec` | string or null |  |  |
| `approval` | Approval or null |  |  |
| `max_iterations` | integer or null |  |  |
| `timeout_seconds` | number or null |  |  |
| `compensation` | string or null |  |  |
| `config` | object |  |  |

### `NodeType`

One of: `function`, `llm`, `tool`, `agent`, `router`, `approval`, `subgraph`, `join`

### `Pattern`

Every value of ``pattern:``; each maps to a PatternBuilder (Steps 4, 12-16, 22-23).

One of: `workflow`, `state_machine`, `prompt_chain`, `router`, `react`, `plan_execute`, `rewoo`, `reflection`, `evaluator_optimiser`, `agentic_rag`, `supervisor`, `hierarchical`, `handoff`, `group_chat`, `map_reduce`, `debate`, `hybrid`

### `RiskTier`

One of: `low`, `medium`, `high`

### `Runtime`

| Field | Type | Required | Default |
|---|---|---|---|
| `adapter` | string |  | `langgraph` |
| `durability` | Durability |  | `checkpoint` |

### `SubAgent`

A worker in a multi-agent spec: another agent.yaml with its own tools and budget.

| Field | Type | Required | Default |
|---|---|---|---|
| `name` | string | yes |  |
| `spec` | string | yes |  |
| `role` | string |  |  |

### `ToolRef`

| Field | Type | Required | Default |
|---|---|---|---|
| `ref` | string | yes |  |
| `alias` | string or null |  |  |
| `approval` | Approval or null |  |  |
| `rate_limit_per_minute` | integer or null |  |  |
| `config` | object |  |  |
