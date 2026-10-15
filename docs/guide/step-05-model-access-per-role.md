# Step 5 · Model access per role

## 1. At a glance

| | |
|---|---|
| **Goal** | Every model call goes through the Project 0 gateway, by role, with tools, structured output, streaming, retries and cost |
| **Time** | About a day |
| **You end up with** | `platform.yaml`, `GatewayModelClient` (`model_client.litellm`), a usage ledger, a compile-time capability check, unit + stand-in tests, a Mac-only real-model test |
| **New words** | role, alias, virtual key, structured output, capability, repair attempt |

## 2. The big idea

A company phone system: staff dial by department ("finance"), not by a person's direct line.
The switchboard (the LiteLLM gateway) connects the call, keeps the bill per department, and
reroutes if a line is down. Agents ask for the **planner** or the **judge**; `platform.yaml`
decides which model that is today.

> *Analogy:* a job title on an org chart (role) versus the person in the job (model). Swap the
> person and the chart stays correct.

## 3. What we're doing

1. Add `platform.yaml`: gateway address, six roles → aliases, capabilities per alias, AUD rate.
2. Build `GatewayModelClient`: chat, tool calling, JSON-schema output, streaming, retries.
3. Record usage and AUD cost per call, per run and per role.
4. Refuse incompatible requests (client) and incompatible specs (compiler).

## 4. Why we're doing this

Budgets, fallbacks, cost accounting and model audit already live in the gateway (Project 0).
Going around it would split the bill and hide spending. Structured output must be reliable
because later steps parse model replies into plans, routes and scores.

> *Why this matters:* per-role virtual keys let the gateway enforce each role's budget even if
> the platform has a bug.

## 5. Key ideas

- **Role** — what a model is for (planner, executor, router, judge, summariser, default).
- **Alias** — the gateway's name for a model (`local-qwen-quality`).
- **Virtual key** — a gateway key with its own model allowlist and budget (created in Step 1).
- **Structured output** — asking the model for JSON matching a schema, then checking it.
- **Repair attempt** — if the JSON is wrong, send the error back once and ask again.
- **Capability** — what a model can do; the table lives in `platform.yaml`.

## 6. How to do it

```bash
cat platform.yaml                                   # roles, capabilities, AUD rate
uv run pytest libs/agent-runtime                    # unit tests with a mocked gateway
just standin-up; set -a; . tests/stack/standin.env; set +a
uv run pytest -m integration libs/agent-runtime     # six roles, streaming, structured output
```

On the Mac, against the real local models:

```bash
P0_REAL_MODELS=1 just with-secrets uv run pytest -m mac libs/agent-runtime
```

Using the client in code:

```python
from agent_contracts.models import ChatRequest, Message, Role
from agent_runtime.models import GatewayModelClient

client = GatewayModelClient(spec_models={"planner": "local-qwen-quality"})
reply = await client.chat(
    ChatRequest(
        model="planner",
        messages=(Message(role=Role.USER, content="Plan the report."),),
        response_schema={
            "type": "object",
            "properties": {"steps": {"type": "array"}},
            "required": ["steps"],
        },
        metadata={"run_id": run_id, "tenant_id": tenant},
    )
)
client.ledger.total(run_id)  # Usage(input_tokens=…, output_tokens=…, cost_aud=…)
```

## 7. Benefits

- One audited path for every model call; per-role budgets enforced by the gateway.
- Model swaps are a config change.
- Bad structured output is repaired once or fails clearly — never silently.
- Costs are reported in AUD with the rate's date.

## 8. Drawbacks and risks

- The gateway is on the critical path; its availability matters.
- Capability values in `platform.yaml` must match reality (VERIFY-ON-MAC).
- A fixed FX rate drifts; it must be refreshed.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Gateway for every call | Direct provider SDK | Only behind an ADR, e.g. a feature the gateway can't proxy |
| httpx client | OpenAI SDK | If we needed APIs beyond chat completions |
| One repair attempt | Constrained decoding only | When every model supports strict JSON mode reliably |

## 10. Which approach fits which situation

- **Planning, judging:** a stronger alias behind `planner` / `judge`.
- **Routing, summarising:** a fast, cheap alias behind `router` / `summariser`.
- **A spec needing a special model for one role:** override it in the spec's `models:`.

## 11. Common mistakes

- Putting a raw alias on a node instead of a role: it bypasses `platform.yaml`.
- Forgetting the per-role key env var (`LITELLM_KEY_PLANNER`): the client says which one is missing.
- Trusting a reply's JSON without the schema check.

## 12. Done when

| Check | Status | Evidence |
|---|---|---|
| Each role resolves to a model | ✅ | `test_roles_resolve_and_use_their_own_key`; integration `test_every_role_resolves_and_answers` (stand-in) |
| Structured-output test passes on both local Qwen models | ⚠️ **VERIFY-ON-MAC** | `test_real_models.py` (skipped here: no real models). Verified against the stand-in mock in `test_structured_output_through_gateway` and with repair logic in unit tests |
| Per-run token and AUD cost recorded | ✅ | `test_cost_is_converted_to_aud_and_recorded_per_run`; `UsageLedger` |
| Capability check refuses a model without tool calling for a tool-using node | ✅ | client `test_capability_check_…`; compiler `test_capability_check_refuses_model_without_tool_calling` |
| ADR | ✅ | ADR 0015 |

## 13. Recap

Step 5 put every model call behind the Project 0 gateway, addressed by role with a per-role
virtual key; added tool calling, schema-checked structured output with one repair attempt,
streaming, retries and per-call AUD cost recorded per run and role; and made both the client and
the compiler refuse a model that lacks a capability a node needs.
