"""Step 5 "Done when": structured output passes on BOTH local Qwen models.

Only meaningful against Project 0's real gateway on the Mac:
    P0_REAL_MODELS=1 just with-secrets uv run pytest -m mac libs/agent-runtime
"""

import json
import os

import pytest
from agent_contracts.models import ChatRequest, Message, Role
from agent_runtime.models import GatewayModelClient
from agent_spec.platform import load_platform

pytestmark = [
    pytest.mark.mac,
    pytest.mark.skipif(
        os.environ.get("P0_REAL_MODELS") != "1",
        reason="needs Project 0's real models (VERIFY-ON-MAC)",
    ),
]
SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "country": {"type": "string"},
        "population_millions": {"type": "number"},
    },
    "required": ["city", "country", "population_millions"],
    "additionalProperties": False,
}


@pytest.mark.parametrize("alias", ["local-qwen-fast", "local-qwen-quality"])
async def test_structured_output_on_local_qwen(alias: str) -> None:
    client = GatewayModelClient(load_platform())
    resp = await client.chat(
        ChatRequest(
            model=alias,
            messages=(
                Message(role=Role.USER, content="Give the largest city in Australia as JSON."),
            ),
            response_schema=SCHEMA,
            temperature=0,
        )
    )
    data = json.loads(resp.message.content)
    assert set(data) == {"city", "country", "population_millions"}
    assert resp.usage.input_tokens > 0
