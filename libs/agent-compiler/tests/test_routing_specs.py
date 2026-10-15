"""Step 15: routers chosen in agent.yaml (rules, embedding classifier, LLM) on both adapters."""

import json
from pathlib import Path

import pytest
from agent_compiler.run import run
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.models import Message, Role, RunStatus
from agent_contracts.testing import ctx
from agent_spec import load_spec_text
from opentelemetry.sdk.trace import TracerProvider

ADAPTERS = ["langgraph", "agent_framework"]
PROVIDER = TracerProvider()


def spec(adapter: str, router_config: str, components: str = "{}") -> str:
    return f"""
spec_version: 1
name: router-test
version: 0.1.0
owner: agent-platform
description: Routes support requests.
risk_tier: low
autonomy: workflow
pattern: workflow
runtime: {{ adapter: {adapter}, durability: memory }}
components: {components}
budgets: {{ max_steps: 10, max_wall_seconds: 30, max_tokens: 10000, max_cost_aud: 1.0 }}
graph:
  entry: triage
  nodes:
    - id: triage
      type: router
      routes: [billing, technical]
      config: {router_config}
    - {{ id: billing, type: function, fn: identity }}
    - {{ id: technical, type: function, fn: identity }}
  edges: ["triage -> billing: billing", "triage -> technical: technical"]
"""


async def _route(text: str, adapter: str, **kw: object) -> str:
    result, _ = await run(load_spec_text(spec(adapter, **kw)), {"text": text}, ctx(), PROVIDER)  # type: ignore[arg-type]
    assert result.status is RunStatus.SUCCEEDED, result.state.error
    return str(result.state.values["triage"]["route"])


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_rules_router_from_the_spec(adapter: str) -> None:
    cfg = '{ router: rules, template: "$input.text", default: technical, rules: [{ route: billing, any: ["invoice", "refund"] }] }'
    assert await _route("refund please", adapter, router_config=cfg) == "billing"
    assert await _route("it crashed", adapter, router_config=cfg) == "technical"


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_embedding_router_from_an_examples_file(adapter: str, tmp_path: Path) -> None:
    examples = tmp_path / "examples.jsonl"
    rows = [
        {"text": "my invoice is wrong", "route": "billing"},
        {"text": "refund the charge on my bill", "route": "billing"},
        {"text": "the app crashes on login", "route": "technical"},
        {"text": "error 500 when saving", "route": "technical"},
    ]
    examples.write_text("\n".join(json.dumps(r) for r in rows))
    text = load_spec_text(
        spec(
            adapter,
            router_config=f'{{ router: embedding, template: "$input.text", examples_file: {examples.name} }}',
            components="{ embedder: hashing }",
        )
    )
    result, _ = await run(
        text,
        {"text": "the app shows error 500"},
        ctx(),
        PROVIDER,
        spec_path=tmp_path / "agent.yaml",
    )
    assert result.status is RunStatus.SUCCEEDED, result.state.error
    assert result.state.values["triage"]["route"] == "technical"


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_llm_router_is_the_default(adapter: str) -> None:
    model = ScriptedModelClient([Message(role=Role.ASSISTANT, content='{"route": "billing"}')])
    result, _ = await run(
        load_spec_text(spec(adapter, router_config='{ template: "$input.text" }')),
        {"text": "invoice"},
        ctx(),
        PROVIDER,
        model=model,
    )
    assert result.state.values["triage"]["route"] == "billing"
    assert model.requests[0].model == "router"
