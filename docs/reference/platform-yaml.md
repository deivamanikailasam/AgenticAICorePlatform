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
| `mcp` | Mcp |  | `{'servers': []}` |

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

### `Mcp`

| Field | Type | Required | Default |
|---|---|---|---|
| `servers` | list of McpServer |  |  |

### `McpAuth`

How the platform authenticates to a remote MCP server.

| Field | Type | Required | Default |
|---|---|---|---|
| `kind` | `none` \| `oauth_client_credentials` \| `bearer` \| `on_behalf_of` |  | `none` |
| `issuer` | string or null |  |  |
| `audience` | string or null |  |  |
| `client_id` | string or null |  |  |
| `client_secret` | string or null |  |  |
| `scope` | string or null |  |  |
| `token` | string or null |  |  |

### `McpServer`

One allowlisted MCP server. Servers not listed here are refused.

| Field | Type | Required | Default |
|---|---|---|---|
| `name` | string | yes |  |
| `transport` | `http` \| `stdio` | yes |  |
| `url` | string or null |  |  |
| `command` | string or null |  |  |
| `args` | list of string |  |  |
| `env` | object |  |  |
| `env_passthrough` | list of string |  |  |
| `protocol` | `auto` \| `legacy` \| `2026-07-28` |  | `auto` |
| `trusted` | boolean |  | `False` |
| `allowed_tools` | list of string or null |  |  |
| `side_effects` | object |  |  |
| `auth` | McpAuth |  | `{'kind': 'none', 'issuer': None, 'audience': None, 'client_id': None, 'client_secret': None, 'scope': None, 'token': None}` |
| `timeout_seconds` | number |  | `30` |

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
