"""Step 4 "Done when": the hello workflow runs end to end with a trace in Tempo."""

import asyncio
import os
from pathlib import Path

import httpx
import pytest
from agent_compiler.run import run
from agent_contracts.models import RunContext, RunStatus
from agent_spec import load_spec

pytestmark = pytest.mark.integration
HELLO = Path(__file__).resolve().parents[3] / "agents/hello-workflow/agent.yaml"


async def test_hello_trace_reaches_tempo() -> None:
    ctx = RunContext(tenant_id="t1", user_id="u1", agent_id="hello-workflow")
    result, trace_id = await run(load_spec(HELLO), {"name": "Tempo"}, ctx)
    assert result.status is RunStatus.SUCCEEDED
    url = f"{os.environ.get('P0_TEMPO_URL', 'http://127.0.0.1:3200')}/api/v2/traces/{trace_id}"
    names: set[str] = set()
    async with httpx.AsyncClient(timeout=5) as client:
        for _ in range(30):
            resp = await client.get(url)
            if resp.status_code == 200:
                trace = resp.json().get("trace", {})
                names = {
                    s["name"]
                    for rs in trace.get("resourceSpans", [])
                    for ss in rs["scopeSpans"]
                    for s in ss["spans"]
                }
                if "invoke_agent hello-workflow" in names:
                    break
            await asyncio.sleep(1)
    assert {"invoke_agent hello-workflow", "execute_node greet"} <= names
