"""Validation gates on LLM steps (Step 15)."""

import json
from typing import Any

import pytest
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.models import Message, NodeKind, PlanNode, Role
from agent_contracts.testing import ctx
from agent_runtime.executors import LlmNodeExecutor
from agent_runtime.gates import GateFailedError, gate_reasons

SCHEMA = {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]}


def say(obj: Any) -> Message:
    return Message(role=Role.ASSISTANT, content=obj if isinstance(obj, str) else json.dumps(obj))


def node(gate: dict[str, Any], schema: dict[str, Any] | None = SCHEMA) -> PlanNode:
    cfg: dict[str, Any] = {"template": "Classify: $input.text", "gate": gate}
    if schema:
        cfg["output_schema"] = schema
    return PlanNode(id="classify", kind=NodeKind.LLM, config={"config": cfg})


VALUES = {"input": {"text": "server down"}}


async def test_output_that_passes_goes_through_with_one_call() -> None:
    model = ScriptedModelClient([say({"label": "urgent"})])
    gate = {"checks": [{"path": "label", "one_of": ["urgent", "normal"]}]}
    assert await LlmNodeExecutor(model)(node(gate), VALUES, ctx()) == {"label": "urgent"}
    assert len(model.requests) == 1


async def test_failing_output_is_sent_back_with_the_reasons_then_accepted() -> None:
    model = ScriptedModelClient([say({"label": "panic"}), say({"label": "urgent"})])
    gate = {"checks": [{"path": "label", "one_of": ["urgent", "normal"]}]}
    out = await LlmNodeExecutor(model)(node(gate), VALUES, ctx())
    assert out == {"label": "urgent"}
    retry = model.requests[1].messages
    assert retry[-2].content == json.dumps({"label": "panic"})
    assert "not one of ['urgent', 'normal']" in retry[-1].content


async def test_gate_fails_the_node_after_max_retries() -> None:
    model = ScriptedModelClient([say("not json"), say({"other": 1}), say({"label": "x"})])
    with pytest.raises(GateFailedError) as err:
        await LlmNodeExecutor(model)(node({"schema": SCHEMA, "max_retries": 1}), VALUES, ctx())
    assert err.value.reasons == ["schema: 'label' is a required property"]
    assert len(model.requests) == 2
    assert "not valid JSON" in model.requests[1].messages[-1].content


async def test_text_checks_and_a_gate_function() -> None:
    def no_new_numbers(output: Any, values: Any) -> Any:
        return True if not any(c.isdigit() for c in output) else "the summary adds numbers"

    gate = {
        "checks": [{"min_length": 10}, {"not_pattern": "(?i)as an ai"}],
        "fn": "g.no_numbers",
    }
    fns = {"g.no_numbers": no_new_numbers}
    assert gate_reasons(gate, "As an AI7", {}, fns) == [
        "output shorter than 10 characters",
        "output matches the forbidden /(?i)as an ai/",
        "the summary adds numbers",
    ]
    model = ScriptedModelClient([say("The server is down for everyone.")])
    out = await LlmNodeExecutor(model, fns)(node(gate, schema=None), VALUES, ctx())
    assert out == "The server is down for everyone."
