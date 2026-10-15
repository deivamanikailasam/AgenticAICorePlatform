"""The worker serves exactly the ``durability: temporal`` agents, under the clients' handles."""

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from agent_api.runs import RunManager
from agent_compiler.compiler import compile_spec
from agent_contracts.models import RunStatus
from agent_contracts.testing import ctx
from agent_runtime.temporal.runtime import TemporalRuntime
from agent_spec import load_spec_text
from agent_worker.worker import serve, temporal_agents
from temporalio.client import Client

ROOT = Path(__file__).resolve().parents[3]
HELLO = (ROOT / "agents/hello-workflow/agent.yaml").read_text()


class Parts:
    def __init__(self, runner: Any) -> None:
        self.runner = runner


class FakeAssembly:
    async def build(self, spec: Any, plan: Any) -> Parts:
        from agent_compiler.run import functions_for
        from agent_runtime.nodes import NodeRunner

        return Parts(NodeRunner(functions_for(plan)))


def durable_hello() -> Any:
    text = HELLO.replace("durability: memory", "durability: temporal")
    return load_spec_text(text.replace("name: hello-workflow", "name: hello-durable"))


def test_only_temporal_agents_are_served() -> None:
    agents = {**RunManager.load_agents(ROOT / "agents"), "hello-durable": durable_hello()}
    assert list(temporal_agents(agents)) == ["hello-durable"]


async def test_serve_registers_each_plan_with_its_assembled_runner() -> None:
    rt = TemporalRuntime()
    handles = await serve(rt, FakeAssembly(), {"hello-durable": durable_hello()})  # type: ignore[arg-type]
    assert handles == ["hello-durable@0.1.0"]
    plan, runner = rt.activities.runners["hello-durable@0.1.0"]
    node = plan.node("greet")
    result = await runner.run(node, {"input": {"name": "Jo"}}, ctx())
    assert result.value == {"greeting": "hello, Jo"}


@pytest.mark.integration
async def test_a_client_without_runners_starts_a_run_the_worker_executes() -> None:
    address = os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")
    try:
        client = await asyncio.wait_for(Client.connect(address), 5)
    except Exception as exc:  # any failure to connect means "no server here"
        if os.environ.get("AGENT_REQUIRE_TEMPORAL"):
            raise
        pytest.skip(f"no Temporal server at {address} ({exc}); run `just up-temporal`")
    queue = f"test-worker-{uuid.uuid4().hex[:8]}"
    worker = TemporalRuntime(client=client, task_queue=queue)
    await serve(worker, FakeAssembly(), {"hello-durable": durable_hello()})  # type: ignore[arg-type]
    caller = TemporalRuntime(client=client, task_queue=queue)  # e.g. the run API
    handle = await caller.build(compile_spec(durable_hello()))
    async with worker.worker():
        result = await caller.invoke(
            handle, {"name": "Jo"}, ctx(run_id=f"r-{uuid.uuid4().hex[:8]}")
        )
    assert result.status is RunStatus.SUCCEEDED
    assert result.output == {"greeting": "hello, Jo"}
