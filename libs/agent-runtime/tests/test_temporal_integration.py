"""Step 19 against a Temporal server (``just up-temporal``).

* the ``durable_executor.temporal`` contract;
* an agent run that pauses for approval, survives a worker stop and a server restart, then
  finishes on a new worker, and its history shows each side-effecting node ran once;
* a 24-hour wait for the approval in Temporal's time-skipping test server (the server binary is
  downloaded on first use; set ``AGENT_REQUIRE_TEMPORAL_TIMESKIP=1`` to fail rather than skip
  when it cannot be fetched, as CI does).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from agent_contracts.models import ExecutablePlan, NodeKind, PlanEdge, PlanNode, RunStatus
from agent_contracts.testing import DurableExecutorContract, ctx
from agent_runtime.nodes import NodeRunner
from agent_runtime.temporal.runtime import TemporalDurableExecutor, TemporalRuntime
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client

pytestmark = pytest.mark.integration
ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")
ROOT = Path(__file__).resolve().parents[3]


async def connect() -> Client:
    try:
        return await asyncio.wait_for(Client.connect(ADDRESS), 5)
    except Exception as exc:
        if os.environ.get("AGENT_REQUIRE_TEMPORAL"):
            raise
        pytest.skip(f"no Temporal server at {ADDRESS} ({exc}); run `just up-temporal`")


class TestTemporalDurableExecutorContract(DurableExecutorContract):
    @pytest.fixture(autouse=True)
    async def _worker(self) -> AsyncIterator[None]:
        self.client = await connect()
        self.queue = f"test-fn-{uuid.uuid4().hex[:8]}"
        async with TemporalDurableExecutor(client=self.client, task_queue=self.queue).worker():
            yield

    def make(self, workflows: dict[str, Any]) -> TemporalDurableExecutor:
        return TemporalDurableExecutor(
            workflows, client=self.client, task_queue=self.queue, id_prefix=f"{self.queue}-"
        )


PAYMENT = ExecutablePlan(
    name="pay",
    nodes=(
        PlanNode(id="record", kind=NodeKind.FUNCTION, config={"fn": "record"}),
        PlanNode(id="approve", kind=NodeKind.APPROVAL, config={}),
        PlanNode(id="pay", kind=NodeKind.FUNCTION, config={"fn": "pay"}),
    ),
    edges=(
        PlanEdge(source="record", target="approve"),
        PlanEdge(source="approve", target="pay", condition="ok"),
    ),
    entry="record",
    exits=("pay",),
)


def payment_runner(effects: Counter[str]) -> NodeRunner:
    def record(values: dict[str, Any]) -> dict[str, Any]:
        effects["record"] += 1
        return {"invoice": values["input"]["invoice"]}

    def pay(values: dict[str, Any]) -> str:
        effects["pay"] += 1
        return f"paid {values['record']['invoice']}"

    return NodeRunner({"record": record, "pay": pay})


def restart_server() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is needed to restart the Temporal server")
    compose = ["docker", "compose", "-f", str(ROOT / "infra/compose/compose.yaml")]
    subprocess.run([*compose, "--profile", "temporal", "restart", "temporal"], check=True)
    subprocess.run(
        [*compose, "--profile", "temporal", "up", "-d", "--wait", "temporal"], check=True
    )


async def scheduled_nodes(client: Client, run_id: str) -> Counter[str]:
    """How many times the workflow history scheduled ``run_node`` for each node."""
    counts: Counter[str] = Counter()
    converter = client.data_converter
    async for event in client.get_workflow_handle(f"agent-run-{run_id}").fetch_history_events():
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            payloads = event.activity_task_scheduled_event_attributes.input.payloads
            (req,) = await converter.decode(payloads)
            counts[req["node_id"]] += 1
    return counts


async def test_an_approval_wait_survives_a_worker_stop_and_a_server_restart() -> None:
    await connect()
    effects: Counter[str] = Counter()
    queue = f"test-run-{uuid.uuid4().hex[:8]}"
    run = ctx(run_id=f"r-{uuid.uuid4().hex[:8]}")

    first = TemporalRuntime(payment_runner(effects), address=ADDRESS, task_queue=queue)
    handle = await first.build(PAYMENT)
    async with first.worker():
        paused = await first.invoke(handle, {"invoice": "INV-7"}, run)
    assert paused.status is RunStatus.INTERRUPTED
    assert paused.state.next_nodes == ("approve",)
    assert effects == Counter(record=1)

    restart_server()  # worker already stopped; now the server goes down and comes back

    second = TemporalRuntime(payment_runner(effects), address=ADDRESS, task_queue=queue)
    handle = await second.build(PAYMENT)
    async with second.worker():
        assert (await second.get_state(run.run_id)).next_nodes == ("approve",)
        done = await second.resume(handle, run.run_id, {"approved": True, "by": "jo"}, run)
    assert done.status is RunStatus.SUCCEEDED
    assert done.output == "paid INV-7"
    assert effects == Counter(record=1, pay=1), "nothing ran twice"
    history = await scheduled_nodes(await second.client(), run.run_id)
    assert history == Counter(record=1, pay=1), "the history has no duplicate side effects"


async def test_a_run_waits_24_hours_for_an_approval_then_finishes() -> None:
    from temporalio.testing import WorkflowEnvironment

    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:
        if os.environ.get("AGENT_REQUIRE_TEMPORAL_TIMESKIP"):
            raise
        pytest.skip(f"time-skipping test server unavailable here ({exc})")
    async with env:
        effects: Counter[str] = Counter()
        rt = TemporalRuntime(payment_runner(effects), client=env.client, task_queue="ts")
        handle = await rt.build(PAYMENT)
        run = ctx(run_id="r-24h")
        async with rt.worker():
            paused = await rt.invoke(handle, {"invoice": "INV-24"}, run)
            assert paused.status is RunStatus.INTERRUPTED
            await env.sleep(timedelta(hours=24))
            assert (await rt.get_state(run.run_id)).next_nodes == ("approve",), "still waiting"
            done = await rt.resume(handle, run.run_id, {"approved": True}, run)
        assert done.status is RunStatus.SUCCEEDED
        assert done.output == "paid INV-24"
        assert effects == Counter(record=1, pay=1)
        assert await scheduled_nodes(env.client, run.run_id) == Counter(record=1, pay=1)


async def test_an_unanswered_approval_expires_after_its_timer() -> None:
    from temporalio.testing import WorkflowEnvironment

    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:
        if os.environ.get("AGENT_REQUIRE_TEMPORAL_TIMESKIP"):
            raise
        pytest.skip(f"time-skipping test server unavailable here ({exc})")
    async with env:
        effects: Counter[str] = Counter()
        rt = TemporalRuntime(payment_runner(effects), client=env.client, task_queue="ts-exp")
        handle = await rt.build(PAYMENT)
        run = ctx(run_id="r-expire")
        async with rt.worker():
            await rt.invoke(handle, {"invoice": "INV-X"}, run)
            await env.sleep(timedelta(days=8))  # past the 7-day default
            state = await rt.get_state(run.run_id)
        assert state.status is RunStatus.FAILED
        assert "approve" in (state.error or "")
        assert effects == Counter(record=1), "an expired approval never pays"
