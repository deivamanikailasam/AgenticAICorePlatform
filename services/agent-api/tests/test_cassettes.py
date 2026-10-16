"""Step 33: record-and-replay of model and tool calls through the run API.

A run is recorded once (here against a scripted model and the real local tools); replaying the
cassette needs neither, and the replayed run's calls are byte-identical to the recording. Policy,
guardrails and budgets still run live during replay. A changed prompt or input is caught, not
silently answered.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_api.app import build_app
from agent_api.assembly import Assembly
from agent_api.runs import RunManager
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import ModelClient, Tool
from agent_contracts.models import Message, Role, ToolCall
from agent_spec import load_spec_text
from agent_spec.platform import load_platform
from agent_testing import (
    Cassette,
    RecordingModelClient,
    RecordingTool,
    ReplayModelClient,
    ReplayTool,
)
from agent_tools.builtin import CalculatorTool, WordCountTool
from agent_tools.registry import InMemoryToolRegistry
from mcp.server.auth.provider import AccessToken

CASSETTES = Path(__file__).parent / "cassettes"
SPEC = """
spec_version: 1
name: counter
version: 0.1.0
owner: platform
description: Works out a sum and counts words.
risk_tier: low
autonomy: hybrid
pattern: hybrid
runtime: { adapter: langgraph, durability: memory }
budgets: { max_steps: 20, max_wall_seconds: 60, max_tokens: 100000, max_cost_aud: 1.0 }
tools:
  - ref: local://platform/calculator
  - ref: local://platform/word_count
graph:
  nodes:
    - { id: work, type: agent, pattern: react, tools: [calculator, word_count], max_iterations: 6 }
"""


def scripted() -> ScriptedModelClient:
    return ScriptedModelClient(
        [
            Message(
                role=Role.ASSISTANT,
                tool_calls=(
                    ToolCall(id="call-1", name="calculator", arguments={"expression": "6*7"}),
                    ToolCall(id="call-2", name="word_count", arguments={"text": "the answer is"}),
                ),
            ),
            Message(role=Role.ASSISTANT, content="6*7 is 42, and the phrase has 3 words."),
        ]
    )


class Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        return AccessToken(
            token=token,
            client_id="t",
            scopes=[],
            expires_at=None,
            claims={"preferred_username": "alice", "groups": ["/acme/ops"]},
        )


async def run_once(
    model: ModelClient,
    tools: list[Tool],
    task: str = "6*7, and count words",
    spec_text: str = SPEC,
) -> Any:
    spec = load_spec_text(spec_text)

    async def registry(s: Any) -> InMemoryToolRegistry:
        reg = InMemoryToolRegistry()
        for t in tools:
            await reg.register(t)
        return reg

    async def no_memory(s: Any) -> None:
        return None

    assembly = Assembly(
        load_platform(),
        RulesPolicyDecisionPoint(),
        MemoryAuditSink(),
        model_factory=lambda s: model,
        registry_factory=registry,
        memory_factory=no_memory,
    )
    manager = RunManager({spec.name: spec}, assembly)
    app = build_app(manager, Verifier())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            f"/v1/agents/{spec.name}/runs",
            json={"input": {"task": task}},
            headers={"Authorization": "Bearer t"},
        )
        assert resp.status_code == 201, resp.text
        run = manager.runs[resp.json()["run_id"]]
        await manager.wait_idle(run)
    return run


def finished(run: Any) -> dict[str, Any]:
    return next(e for e in run.events if e["type"] == "RUN_FINISHED")["result"]["output"]


async def record(path: Path) -> tuple[Any, Cassette]:
    cassette = Cassette(path, "record")
    tools: list[Tool] = [
        RecordingTool(CalculatorTool(), cassette),
        RecordingTool(WordCountTool(), cassette),
    ]
    run = await run_once(RecordingModelClient(scripted(), cassette), tools)
    cassette.save()
    return run, cassette


async def replay(path: Path, task: str = "6*7, and count words") -> tuple[Any, Cassette]:
    cassette = Cassette(path, "replay")
    tools: list[Tool] = [
        ReplayTool(CalculatorTool().spec, cassette),
        ReplayTool(WordCountTool().spec, cassette),
    ]
    return await run_once(ReplayModelClient(cassette), tools, task), cassette


async def test_a_replayed_run_is_byte_identical_to_its_recording(tmp_path: Path) -> None:
    recorded_run, recording = await record(tmp_path / "counter.json")
    assert recorded_run.status == "succeeded"
    assert [c["kind"] for c in recording.calls] == ["model", "tool", "tool", "model"]

    first, replay1 = await replay(tmp_path / "counter.json")
    second, replay2 = await replay(tmp_path / "counter.json")
    assert first.status == second.status == "succeeded"
    assert replay1.transcript() == recording.transcript()  # byte-identical
    assert replay2.transcript() == recording.transcript()
    assert replay1.unused() == 0
    assert finished(first) == finished(second) == finished(recorded_run)


async def test_the_committed_cassette_replays_offline() -> None:
    """The cassette in the repository replays with no model and no tools: CI runs this."""
    path = CASSETTES / "counter.json"
    run, cassette = await replay(path)
    assert run.status == "succeeded", run.events[-1]
    assert cassette.unused() == 0
    recorded = Cassette(path, "replay")
    for call in recorded._recorded:
        recorded.take(call["kind"], call["request"])
    assert cassette.transcript() == recorded.transcript()


async def test_a_changed_run_is_caught_not_answered(tmp_path: Path) -> None:
    await record(tmp_path / "counter.json")
    run, _ = await replay(tmp_path / "counter.json", task="something else entirely")
    assert run.status == "failed"
    error = next(e for e in run.events if e["type"] == "RUN_ERROR")["message"]
    assert "no recorded model call matches this request" in error


@pytest.mark.skipif(
    os.environ.get("AGENT_RECORD_CASSETTES") != "1", reason="set AGENT_RECORD_CASSETTES=1"
)
async def test_rerecord_the_committed_cassette() -> None:
    """``just record-cassettes`` rewrites tests/cassettes/counter.json."""
    await record(CASSETTES / "counter.json")


class NoNetwork:
    """Every socket connection fails: proof that a replay needs no network."""

    def __enter__(self) -> NoNetwork:
        import socket

        self._connect = socket.socket.connect

        def refuse(sock: socket.socket, address: Any) -> None:
            raise OSError(f"network disabled during replay (tried {address})")

        socket.socket.connect = refuse  # type: ignore[method-assign,assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        import socket

        socket.socket.connect = self._connect  # type: ignore[method-assign]


@pytest.mark.integration
async def test_a_run_recorded_against_the_gateway_replays_without_it(tmp_path: Path) -> None:
    """Recorded over HTTP against the stand-in LiteLLM gateway, replayed with every socket
    connection refused: the same calls, byte for byte."""
    from agent_runtime.models import GatewayModelClient
    from agent_spec.platform import Gateway

    base = load_platform()
    cfg = base.model_copy(
        update={"gateway": Gateway(url=os.environ.get("P0_LITELLM_URL", "http://127.0.0.1:4000"))}
    )
    gateway = GatewayModelClient(cfg, keys={"default": os.environ["LITELLM_MASTER_KEY"]})
    path = tmp_path / "gateway.json"
    recording = Cassette(path, "record")
    tools: list[Tool] = [
        RecordingTool(CalculatorTool(), recording),
        RecordingTool(WordCountTool(), recording),
    ]
    recorded = await run_once(RecordingModelClient(gateway, recording), tools)
    recording.save()
    assert recorded.status == "succeeded", recorded.events[-1]
    assert any(c["kind"] == "model" for c in recording.calls)

    with NoNetwork():
        replayed, cassette = await replay(path)
    assert replayed.status == "succeeded", replayed.events[-1]
    assert cassette.transcript() == recording.transcript()
    assert finished(replayed) == finished(recorded)
