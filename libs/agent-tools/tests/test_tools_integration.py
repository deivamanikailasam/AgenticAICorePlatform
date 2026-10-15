"""Postgres registry, Valkey idempotency and S3 spill against Project 0 (or the stand-in)."""

import asyncio
import os
from typing import Any

import pytest
from agent_contracts.interfaces import Tool
from agent_contracts.models import RunContext, SideEffect, ToolCall, ToolSpec
from agent_contracts.testing import ToolRegistryContract
from agent_tools.artefacts import S3ArtefactStore
from agent_tools.builtin import CalculatorTool
from agent_tools.executor import PlatformToolExecutor
from agent_tools.idempotency import ValkeyIdempotencyStore
from agent_tools.registry import PostgresToolRegistry

pytestmark = pytest.mark.integration
CTX = RunContext(tenant_id="t-int", user_id="u1", agent_id="a1")


class TestPostgresRegistryContract(ToolRegistryContract):
    @pytest.fixture(autouse=True)
    def _ns(self, namespace: str, agent_dsn: str) -> None:
        self.ns, self.dsn = namespace, agent_dsn

    def make(self) -> PostgresToolRegistry:
        return PostgresToolRegistry(self.dsn, namespace=self.ns)


async def test_registry_is_shared_across_processes(agent_dsn: str, namespace: str) -> None:
    writer = PostgresToolRegistry(agent_dsn, namespace)
    await writer.register(CalculatorTool())
    reader = PostgresToolRegistry(agent_dsn, namespace)  # another process
    assert [s.name for s in await reader.list()] == ["calculator"]
    with pytest.raises(KeyError, match="no implementation in this process"):
        await reader.get("calculator")
    reader.bind(CalculatorTool())
    assert (await reader.get("calculator")).spec.ref == "local://platform/calculator"


class Counter(Tool):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="count_write", side_effect=SideEffect.WRITE)

    async def invoke(self, arguments: Any, ctx: RunContext) -> Any:
        self.calls += 1
        await asyncio.sleep(0.05)
        return {"n": self.calls}


async def test_valkey_idempotency_across_executors(agent_dsn: str, namespace: str) -> None:
    reg = PostgresToolRegistry(agent_dsn, namespace)
    tool = Counter()
    await reg.register(tool)
    url = os.environ.get("P0_VALKEY_URL", "redis://127.0.0.1:6379/0")
    a = PlatformToolExecutor(reg, idempotency=ValkeyIdempotencyStore(url))
    b = PlatformToolExecutor(reg, idempotency=ValkeyIdempotencyStore(url))  # e.g. another worker
    call = ToolCall(name="count_write", idempotency_key=namespace)
    r1, r2 = await asyncio.gather(a.execute(call, CTX), b.execute(call, CTX))
    assert tool.calls == 1
    assert r1.output == r2.output == {"n": 1}


async def test_large_output_spills_to_s3() -> None:
    store = S3ArtefactStore(
        os.environ.get("P0_S3_ENDPOINT", "http://127.0.0.1:8333"),
        os.environ.get("AGENT_ARTEFACT_BUCKET", "agent-artefacts"),
        os.environ["P0_S3_ACCESS_KEY"],
        os.environ["P0_S3_SECRET_KEY"],
    )
    store.ensure_bucket()
    uri = await store.put("it/hello.json", b'{"hello": "world"}')
    assert uri == "s3://agent-artefacts/it/hello.json"
    assert await store.get(uri) == b'{"hello": "world"}'
