"""Step 33 chaos tests: what runs do when dependencies misbehave.

* model timeout: the gateway client retries, and the run succeeds;
* gateway down: runs fail fast with the reason, the model circuit opens so later runs stop at
  once without hammering the gateway, and once the gateway is back a failed run is retried from
  its checkpoints without repeating the steps that had completed;
* slow tool: the call times out at the tool's limit, the agent gets the timeout as a result and
  finishes; repeated timeouts open the tool's circuit;
* killed worker: covered where the process is really killed (``test_durable_runs.py``:
  SIGKILL mid-run, then resume without repeating the write, on both adapters and on Postgres;
  ``test_api.py``: a running run resumes from its last checkpoint after a restart).

All run offline through the real run API, gateway client and executors; only the network
(an ``httpx`` transport) and the clock are faked.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.interfaces import Tool
from agent_contracts.models import (
    DataClass,
    Message,
    Role,
    RunContext,
    SideEffect,
    ToolCall,
    ToolSpec,
)
from agent_runtime.circuit import CircuitBreaker
from agent_runtime.models import GatewayModelClient
from agent_spec.platform import load_platform


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = _load("test_api")
cassettes = _load("test_cassettes")


class FakeGateway:
    """The network between the platform and LiteLLM: up, down, or timing out N times."""

    def __init__(self) -> None:
        self.mode = "up"
        self.timeouts_left = 0
        self.requests = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.mode == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if self.timeouts_left > 0:
            self.timeouts_left -= 1
            raise httpx.ReadTimeout("model took too long", request=request)
        body = json.loads(request.content)
        if body.get("stream"):
            chunks = [
                {
                    "id": f"chatcmpl-{self.requests}",
                    "choices": [{"delta": {"content": "The total is 42."}}],
                },
                {
                    "id": f"chatcmpl-{self.requests}",
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            ]
            text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        return httpx.Response(
            200,
            json={
                "id": f"chatcmpl-{self.requests}",
                "model": json.loads(request.content)["model"],
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "The total is 42."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    def client(self, spec: Any) -> GatewayModelClient:
        return GatewayModelClient(
            load_platform(),
            spec_models=spec.models,
            keys={"default": "k"},
            transport=httpx.MockTransport(self.handler),
            backoff_seconds=0,
        )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def world(gateway: FakeGateway, clock: Clock) -> Any:
    w = api.durable_world(model=gateway.client)
    w.manager.assembly.model_circuits = CircuitBreaker(failures=3, cooldown=30, clock=clock)
    return w


async def start(w: Any) -> Any:
    async with api.client_for(w) as client:
        resp = await client.post(
            "/v1/agents/pay-total/runs",
            json={"input": {"expression": "40+2"}},
            headers=api.auth("t-alice"),
        )
        assert resp.status_code == 201, resp.text
    run = w.manager.runs[resp.json()["run_id"]]
    await w.manager.wait_idle(run)
    return run


def error_of(run: Any) -> str:
    return str(next(e for e in reversed(run.events) if e["type"] == "RUN_ERROR")["message"])


# ---------------------------------------------------------------- model timeout


async def test_a_model_timeout_is_retried_and_the_run_carries_on() -> None:
    gateway, clock = FakeGateway(), Clock()
    gateway.timeouts_left = 1
    w = world(gateway, clock)
    run = await start(w)
    assert run.status == "interrupted"  # reached the payment approval: the model step worked
    assert gateway.requests == 2  # one timeout, one retry
    assert len(w.sums) == 1
    assert w.manager.assembly.model_circuits.state("model:default") == "closed"


# ---------------------------------------------------------------- gateway down


async def test_a_gateway_outage_fails_fast_opens_the_circuit_and_retry_resumes() -> None:
    gateway, clock = FakeGateway(), Clock()
    w = world(gateway, clock)
    gateway.mode = "down"

    first = await start(w)
    assert first.status == "failed"
    assert "gateway unavailable after 3 attempts (ConnectError" in error_of(first)
    assert len(w.sums) == 1  # the tool step before the model step completed

    for _ in range(2):  # two more runs fail the same way: three failures open the circuit
        assert (await start(w)).status == "failed"
    calls_so_far = gateway.requests
    began = time.perf_counter()
    stopped = await start(w)
    assert gateway.requests == calls_so_far, "an open circuit doesn't call the gateway"
    assert time.perf_counter() - began < 2.0
    assert stopped.status in ("stopped", "failed")
    assert "circuit open: model default failed repeatedly" in json.dumps(stopped.events)

    # the gateway is back and the cool-down has passed: retry the first run
    gateway.mode = "up"
    clock.now += 31
    sums_before = len(w.sums)
    async with api.client_for(w) as client:
        resp = await client.post(f"/v1/runs/{first.run_id}/retry", headers=api.auth("t-alice"))
        assert resp.status_code == 202, resp.text
    await w.manager.wait_idle(first)
    assert first.status == "interrupted"  # carried on to the payment approval
    assert len(w.sums) == sums_before, "the completed tool step was not repeated"
    assert w.manager.assembly.model_circuits.state("model:default") == "closed"


async def test_only_the_owner_may_retry_and_only_a_failed_run() -> None:
    gateway, clock = FakeGateway(), Clock()
    w = world(gateway, clock)
    ok = await start(w)
    async with api.client_for(w) as client:
        not_failed = await client.post(f"/v1/runs/{ok.run_id}/retry", headers=api.auth("t-alice"))
        assert not_failed.status_code == 409
        gateway.mode = "down"
        failed = await start(w)
        other = await client.post(f"/v1/runs/{failed.run_id}/retry", headers=api.auth("t-carol"))
        assert other.status_code in (403, 404)


# ---------------------------------------------------------------- slow tool


class SlowLookup(Tool):
    """A tool that hangs (a slow downstream service)."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="lookup",
            version="1.0.0",
            ref="local://test/lookup",
            owner="test",
            description="Look something up (slowly).",
            input_schema={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
            side_effect=SideEffect.READ,
            data_classification=DataClass.INTERNAL,
            timeout_seconds=0.2,
        )

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        self.calls += 1
        await asyncio.sleep(30)
        return {"never": True}


def others() -> list[Tool]:
    from agent_tools.builtin import CalculatorTool, WordCountTool

    return [CalculatorTool(), WordCountTool()]


SLOW_SPEC = cassettes.SPEC.replace(
    "  - ref: local://platform/word_count\n",
    "  - ref: local://platform/word_count\n  - ref: local://test/lookup\n",
).replace("tools: [calculator, word_count]", "tools: [calculator, word_count, lookup]")


async def test_a_slow_tool_times_out_and_the_agent_finishes() -> None:
    tool = SlowLookup()
    model = ScriptedModelClient(
        [
            Message(
                role=Role.ASSISTANT, tool_calls=(ToolCall(name="lookup", arguments={"q": "x"}),)
            ),
            Message(role=Role.ASSISTANT, content="The lookup timed out, so I answered without it."),
        ]
    )
    began = time.perf_counter()
    run = await cassettes.run_once(model, [*others(), tool], spec_text=SLOW_SPEC)
    assert run.status == "succeeded"
    assert time.perf_counter() - began < 5  # cut at the tool's 0.2 s limit, not its 30 s hang
    assert tool.calls == 1
    seen = json.dumps(model.requests[-1].model_dump(mode="json"))
    assert "timed out after 0.2s" in seen  # the agent was told, as a tool result


async def test_repeated_tool_timeouts_open_the_tool_circuit() -> None:
    tool = SlowLookup()
    calls = [
        Message(role=Role.ASSISTANT, tool_calls=(ToolCall(name="lookup", arguments={"q": str(i)}),))
        for i in range(6)
    ]
    model = ScriptedModelClient(
        [*calls, Message(role=Role.ASSISTANT, content="Gave up on lookup.")]
    )
    run = await cassettes.run_once(
        model,
        [*others(), tool],
        spec_text=SLOW_SPEC.replace("max_iterations: 6", "max_iterations: 10"),
    )
    assert run.status == "succeeded"
    assert tool.calls == 5, "after 5 timeouts the 6th call is refused without reaching the tool"
    assert "unavailable after repeated failures" in json.dumps(
        model.requests[-1].model_dump(mode="json")
    )


# ---------------------------------------------------------------- gateway fallback (stand-in)


@pytest.mark.integration
async def test_a_failing_model_is_served_by_the_gateway_fallback() -> None:
    """The stand-in LiteLLM's ``local-qwen-flaky`` always fails and falls back to
    ``local-qwen-fast`` (tests/stack/config/litellm.yaml): an agent using it still completes."""
    import os

    from agent_contracts.models import ModelCapabilities
    from agent_spec.platform import Gateway
    from agent_testing import Cassette, RecordingModelClient

    base = load_platform()
    caps = {**base.models.capabilities, "local-qwen-flaky": ModelCapabilities(tool_calling=True)}
    cfg = base.model_copy(
        update={
            "gateway": Gateway(url=os.environ.get("P0_LITELLM_URL", "http://127.0.0.1:4000")),
            "models": base.models.model_copy(update={"capabilities": caps}),
        }
    )
    seen = Cassette(Path("/dev/null"), "record")  # only to see what the gateway answered
    model = RecordingModelClient(
        GatewayModelClient(
            cfg,
            spec_models={"default": "local-qwen-flaky"},
            keys={"default": os.environ["LITELLM_MASTER_KEY"]},
        ),
        seen,
    )
    spec = cassettes.SPEC.replace(
        "pattern: hybrid\n", "pattern: hybrid\nmodels: { default: local-qwen-flaky }\n"
    )
    run = await cassettes.run_once(model, others(), spec_text=spec)
    assert run.status == "succeeded", run.events[-1]
    answered = [c["response"] for c in seen.calls if c["kind"] == "model"]
    assert answered
    assert all(r["model"] == "fake-fast" for r in answered)  # served by the fallback deployment
    assert "stand-in fast" in json.dumps(cassettes.finished(run))
