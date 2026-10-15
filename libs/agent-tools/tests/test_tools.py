import asyncio
from typing import Any

import pytest
from agent_contracts.interfaces import Tool
from agent_contracts.models import (
    RetryPolicy,
    RunContext,
    SideEffect,
    ToolCall,
    ToolErrorCode,
    ToolSpec,
)
from agent_contracts.testing import ToolContract, ToolExecutorContract, ToolRegistryContract
from agent_tools.artefacts import MemoryArtefactStore
from agent_tools.builtin import CalculatorTool, ClockTool, WordCountTool, safe_eval
from agent_tools.executor import PlatformToolExecutor
from agent_tools.idempotency import MemoryIdempotencyStore
from agent_tools.registry import InMemoryToolRegistry

CTX = RunContext(tenant_id="t1", user_id="u1", agent_id="a1")


class TestInMemoryRegistry(ToolRegistryContract):
    def make(self) -> InMemoryToolRegistry:
        return InMemoryToolRegistry()


class TestExecutorContract(ToolExecutorContract):
    def make(self, registry: Any) -> PlatformToolExecutor:
        return PlatformToolExecutor(registry)


class TestCalculator(ToolContract):
    def make(self) -> Tool:
        return CalculatorTool()


class TestClock(ToolContract):
    def make(self) -> Tool:
        return ClockTool()


class TestWordCount(ToolContract):
    def make(self) -> Tool:
        return WordCountTool()


class Scripted(Tool):
    """A tool whose behaviour each test sets."""

    def __init__(self, spec: ToolSpec, outcomes: list[Any]) -> None:
        self._spec, self.outcomes, self.calls = spec, outcomes, 0

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def invoke(self, arguments: Any, ctx: RunContext) -> Any:
        self.calls += 1
        await asyncio.sleep(0.01)
        out = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if isinstance(out, Exception):
            raise out
        return out


async def executor(*tools: Tool, **kw: Any) -> PlatformToolExecutor:
    reg = InMemoryToolRegistry()
    for t in tools:
        await reg.register(t)
    return PlatformToolExecutor(reg, **kw)


def test_calculator_refuses_code() -> None:
    assert safe_eval("(2 + 3) * 4") == 20
    for bad in ("__import__('os')", "a + 1", "[1][0]", "9 ** 9 ** 9"):
        with pytest.raises(ValueError, match=r"unsupported|too large|invalid"):
            safe_eval(bad)


async def test_write_tools_require_an_idempotency_key() -> None:
    t = Scripted(ToolSpec(name="create_ticket", side_effect=SideEffect.WRITE), [{"id": 1}])
    ex = await executor(t)
    res = await ex.execute(ToolCall(name="create_ticket"), CTX)
    assert res.error_code is ToolErrorCode.INVALID_ARGUMENTS
    assert "idempotency key" in (res.error or "")
    assert t.calls == 0


async def test_concurrent_duplicates_run_once() -> None:
    t = Scripted(
        ToolSpec(name="create_ticket", side_effect=SideEffect.WRITE), [{"id": 1}, {"id": 2}]
    )
    ex = await executor(t)
    a, b = await asyncio.gather(
        ex.execute(ToolCall(name="create_ticket", idempotency_key="k"), CTX),
        ex.execute(ToolCall(name="create_ticket", idempotency_key="k"), CTX),
    )
    assert t.calls == 1
    assert a.output == b.output == {"id": 1}


async def test_irreversible_is_never_retried_and_failure_is_kept() -> None:
    spec = ToolSpec(
        name="pay",
        side_effect=SideEffect.IRREVERSIBLE,
        retry=RetryPolicy(max_attempts=3, backoff_seconds=0),
    )
    t = Scripted(spec, [RuntimeError("bank timeout"), {"paid": True}])
    ex = await executor(t)
    first = await ex.execute(ToolCall(name="pay", idempotency_key="p1"), CTX)
    again = await ex.execute(ToolCall(name="pay", idempotency_key="p1"), CTX)
    assert not first.ok
    assert again.error == first.error
    assert t.calls == 1, "an irreversible call is attempted once, ever, per key"


async def test_idempotent_reads_retry_per_policy() -> None:
    spec = ToolSpec(name="lookup", retry=RetryPolicy(max_attempts=3, backoff_seconds=0))
    t = Scripted(spec, [RuntimeError("flaky"), RuntimeError("flaky"), {"ok": 1}])
    res = await (await executor(t)).execute(ToolCall(name="lookup"), CTX)
    assert res.ok
    assert t.calls == 3


async def test_failed_write_releases_its_key_for_a_retry() -> None:
    t = Scripted(
        ToolSpec(name="draft", side_effect=SideEffect.WRITE), [RuntimeError("db down"), {"id": 9}]
    )
    ex = await executor(t)
    assert not (await ex.execute(ToolCall(name="draft", idempotency_key="d"), CTX)).ok
    assert (await ex.execute(ToolCall(name="draft", idempotency_key="d"), CTX)).output == {"id": 9}


async def test_output_schema_is_enforced() -> None:
    spec = ToolSpec(name="typed", output_schema={"type": "object", "required": ["n"]})
    res = await (await executor(Scripted(spec, [{"m": 1}]))).execute(ToolCall(name="typed"), CTX)
    assert res.error_code is ToolErrorCode.FAILED
    assert "schema" in (res.error or "")


async def test_large_outputs_spill_to_the_artefact_store() -> None:
    store = MemoryArtefactStore()
    t = Scripted(ToolSpec(name="dump"), [{"rows": ["x" * 100] * 100}])
    ex = await executor(t, artefacts=store, max_inline_bytes=1000)
    res = await ex.execute(ToolCall(name="dump"), CTX)
    assert res.ok
    assert res.artefact_uri is not None
    assert res.artefact_uri.startswith("memory://tool-outputs/t1/")
    assert res.output["truncated"] is True
    assert len(res.output["preview"]) <= 2000
    assert len(await store.get(res.artefact_uri)) == res.output["bytes"]


async def test_argument_errors_say_where() -> None:
    res = await (await executor(CalculatorTool())).execute(
        ToolCall(name="calculator", arguments={"expression": 5}), CTX
    )
    assert res.error_code is ToolErrorCode.INVALID_ARGUMENTS
    assert res.error is not None
    assert res.error.startswith("expression:")


class _FinishesBetweenReads(MemoryIdempotencyStore):
    """The first caller stores its result exactly between the waiter's ``get`` and ``pending``."""

    def __init__(self, result: Any) -> None:
        super().__init__()
        self.result = result
        self.gets = 0

    async def claim(self, key: str, ttl_seconds: int) -> bool:
        return False  # someone else holds the key

    async def get(self, key: str) -> Any:
        self.gets += 1
        return None if self.gets == 1 else self.result

    async def pending(self, key: str) -> bool:
        return False  # by now the value is the stored result, not the pending marker


async def test_a_duplicate_that_finishes_between_reads_still_returns_the_first_result() -> None:
    from agent_contracts.models import ToolResult

    reg = InMemoryToolRegistry()
    await reg.register(CalculatorTool())
    first = ToolResult(call_id="c-1", tool="calculator", ok=True, output={"result": 2.0})
    ex = PlatformToolExecutor(reg, idempotency=_FinishesBetweenReads(first))
    call = ToolCall(name="calculator", arguments={"expression": "1+1"}, idempotency_key="k")
    result = await ex.execute(call, CTX)
    assert (result.ok, result.output, result.call_id) == (True, {"result": 2.0}, call.id)
