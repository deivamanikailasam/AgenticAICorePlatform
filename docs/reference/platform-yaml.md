# `platform.yaml` reference (platform_version 1)

Generated from `agent_spec.platform` by `just spec-reference` — do not edit by hand.
Specs name model *roles*; `models.roles` maps each role to a LiteLLM gateway alias, and
`models.capabilities` records what each alias can do (checked at compile time).
Costs from the gateway are in USD and converted with `currency.usd_to_aud`, whose
`source` and `as_of` date are shown on every cost report.

### `PlatformConfig (root)`

| Field | Type | Required | Default |
|---|---|---|---|
| `platform_version` | `1` |  | `1` |
| `gateway` | Gateway |  | `{'url': 'http://127.0.0.1:4000', 'key_env_prefix': 'LITELLM_KEY_', 'timeout_seconds': 60.0, 'max_retries': 2}` |
| `models` | Models | yes |  |
| `currency` | Currency | yes |  |
| `components` | object |  |  |

### `Currency`

LiteLLM accounts cost in USD; budgets and reports are in AUD.

| Field | Type | Required | Default |
|---|---|---|---|
| `usd_to_aud` | number | yes |  |
| `source` | string | yes |  |
| `as_of` | string | yes |  |

### `Gateway`

| Field | Type | Required | Default |
|---|---|---|---|
| `url` | string |  | `http://127.0.0.1:4000` |
| `key_env_prefix` | string |  | `LITELLM_KEY_` |
| `timeout_seconds` | number |  | `60` |
| `max_retries` | integer |  | `2` |

### `ModelCapabilities`

| Field | Type | Required | Default |
|---|---|---|---|
| `tool_calling` | boolean |  | `False` |
| `structured_output` | boolean |  | `False` |
| `streaming` | boolean |  | `False` |
| `context_window` | integer |  | `8192` |

### `Models`

| Field | Type | Required | Default |
|---|---|---|---|
| `roles` | object | yes |  |
| `capabilities` | object |  |  |
| `fallbacks` | object |  |  |
