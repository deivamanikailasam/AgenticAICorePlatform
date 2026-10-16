"""Step 33: cassettes record model and tool calls and replay them exactly."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.models import ChatRequest, Message, Role, RunContext
from agent_testing import (
    Cassette,
    CassetteMismatchError,
    RecordingModelClient,
    RecordingTool,
    ReplayModelClient,
    ReplayTool,
)
from agent_testing.cassette import ReplayedToolError
from agent_tools.builtin import CalculatorTool

CTX = RunContext(tenant_id="t", user_id="u", agent_id="a", agent_version="1")


def ask(text: str, run: str = "r1") -> ChatRequest:
    return ChatRequest(
        model="default",
        messages=(Message(role=Role.USER, content=text),),
        metadata={"run_id": run},
    )


async def test_model_calls_replay_and_ignore_per_run_metadata(tmp_path: Path) -> None:
    rec = Cassette(tmp_path / "c.json", "record")
    model = RecordingModelClient(
        ScriptedModelClient(
            [
                Message(role=Role.ASSISTANT, content="one"),
                Message(role=Role.ASSISTANT, content="two"),
            ]
        ),
        rec,
    )
    assert (await model.chat(ask("hi"))).message.content == "one"
    assert (await model.chat(ask("hi"))).message.content == "two"  # same request, next answer
    rec.save()

    play = Cassette(tmp_path / "c.json", "replay")
    replay = ReplayModelClient(play)
    # a different run id is not a different request
    assert (await replay.chat(ask("hi", run="r2"))).message.content == "one"
    assert (await replay.chat(ask("hi", run="r3"))).message.content == "two"
    assert play.transcript() == rec.transcript()
    with pytest.raises(CassetteMismatchError, match="no recorded model call"):
        await replay.chat(ask("hi"))  # all used up


async def test_a_changed_request_names_where_it_differs(tmp_path: Path) -> None:
    rec = Cassette(tmp_path / "c.json", "record")
    await RecordingModelClient(
        ScriptedModelClient([Message(role=Role.ASSISTANT, content="x")]), rec
    ).chat(ask("pay invoice 1001"))
    rec.save()
    with pytest.raises(CassetteMismatchError) as err:
        await ReplayModelClient(Cassette(tmp_path / "c.json", "replay")).chat(
            ask("pay invoice 1002")
        )
    message = str(err.value)
    assert "recorded:" in message
    assert "invoice 1001" in message  # what was recorded
    assert "invoice 1002" in message  # what was asked now
    assert "Re-record the cassette" in str(err.value)


async def test_tool_results_and_errors_replay(tmp_path: Path) -> None:
    rec = Cassette(tmp_path / "c.json", "record")
    tool = RecordingTool(CalculatorTool(), rec)
    assert await tool.invoke({"expression": "2+3"}, CTX) == await CalculatorTool().invoke(
        {"expression": "2+3"}, CTX
    )
    with pytest.raises(Exception, match=r".") as boom:
        await tool.invoke({"expression": "1/0"}, CTX)
    rec.save()

    play = Cassette(tmp_path / "c.json", "replay")
    replay = ReplayTool(CalculatorTool().spec, play)
    assert await replay.invoke({"expression": "2+3"}, CTX) == await CalculatorTool().invoke(
        {"expression": "2+3"}, CTX
    )
    with pytest.raises(ReplayedToolError) as again:
        await replay.invoke({"expression": "1/0"}, CTX)
    assert again.value.kind == type(boom.value).__name__
    assert str(again.value) == str(boom.value)
    with pytest.raises(CassetteMismatchError, match="no recorded tool call"):
        await replay.invoke({"expression": "9*9"}, CTX)


async def test_streams_replay_chunk_for_chunk(tmp_path: Path) -> None:
    rec = Cassette(tmp_path / "c.json", "record")
    model = RecordingModelClient(
        ScriptedModelClient([Message(role=Role.ASSISTANT, content="streamed")]), rec
    )
    first = [c async for c in model.stream(ask("go"))]
    rec.save()
    again = [
        c
        async for c in ReplayModelClient(Cassette(tmp_path / "c.json", "replay")).stream(ask("go"))
    ]
    assert again == first


def test_an_unknown_cassette_version_is_refused(tmp_path: Path) -> None:
    (tmp_path / "c.json").write_text('{"version": 99, "calls": []}')
    with pytest.raises(ValueError, match="version 99"):
        Cassette(tmp_path / "c.json", "replay")
