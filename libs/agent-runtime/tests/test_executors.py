import json
from typing import Any

import pytest
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.models import Message, NodeKind, PlanNode, Role
from agent_contracts.testing import ctx
from agent_runtime.executors import (
    LlmNodeExecutor,
    RouterNodeExecutor,
    lookup,
    render,
    resolve,
)

VALUES: dict[str, Any] = {
    "input": {"invoice": {"id": "inv-1", "lines": [{"amount": 5}]}},
    "validate": {"total": 10},
}


def test_resolve_reads_paths_and_never_evaluates() -> None:
    args = {"id": "$input.invoice.id", "first": "${input.invoice.lines.0.amount}", "lit": "$$5"}
    assert resolve(args, VALUES) == {"id": "inv-1", "first": 5, "lit": "$5"}
    assert resolve(["$validate.total", 3], VALUES) == [10, 3]
    with pytest.raises(KeyError, match=r"no value at 'input\.__class__'"):
        resolve("$input.__class__", VALUES)
    with pytest.raises(KeyError):
        lookup(VALUES, "validate.total.real")


def test_render_inserts_values_as_text_only() -> None:
    text = render("Invoice ${input.invoice.id} totals $validate.total: $validate", VALUES)
    assert text == 'Invoice inv-1 totals 10: {"total": 10}'
    hostile = {"input": {"note": "${validate.total} {__import__('os')}"}}
    assert render("Note: $input.note", hostile) == "Note: ${validate.total} {__import__('os')}"


def _node(kind: NodeKind, **config: Any) -> PlanNode:
    return PlanNode(id="n", kind=kind, config=config)


async def test_llm_node_structured_output() -> None:
    model = ScriptedModelClient([Message(role=Role.ASSISTANT, content='{"ok": true}')])
    node = _node(
        NodeKind.LLM,
        model="planner",
        config={
            "template": "Check $input.invoice.id",
            "system": "Be brief",
            "output_schema": {"type": "object"},
        },
    )
    out = await LlmNodeExecutor(model)(node, VALUES, ctx())
    assert out == {"ok": True}
    req = model.requests[0]
    assert req.model == "planner"
    assert [m.role for m in req.messages] == [Role.SYSTEM, Role.USER]
    assert req.messages[1].content == "Check inv-1"
    assert req.response_schema == {"type": "object"}


async def test_llm_node_refuses_prompt_refs_until_the_registry_exists() -> None:
    node = _node(NodeKind.LLM, prompt="prompt://invoice/resolve@3")
    with pytest.raises(NotImplementedError, match="Step 24"):
        await LlmNodeExecutor(ScriptedModelClient())(node, VALUES, ctx())


async def test_router_only_returns_a_declared_route() -> None:
    model = ScriptedModelClient(
        [
            Message(role=Role.ASSISTANT, content=json.dumps({"route": "approve"})),
            Message(role=Role.ASSISTANT, content=json.dumps({"route": "wire-money"})),
        ]
    )
    node = _node(NodeKind.ROUTER, routes=["approve", "reject"])
    router = RouterNodeExecutor(model)
    out = await router(node, VALUES, ctx())
    assert (out["_outcome"], out["route"]) == ("approve", "approve")
    schema = model.requests[0].response_schema
    assert schema is not None
    assert schema["properties"]["route"]["enum"] == ["approve", "reject"]
    with pytest.raises(ValueError, match="not one of"):
        await router(node, VALUES, ctx())


async def test_router_by_function() -> None:
    node = _node(NodeKind.ROUTER, routes=["big", "small"], fn="pick")
    router = RouterNodeExecutor(functions={"pick": lambda v: "big"})
    assert (await router(node, VALUES, ctx()))["_outcome"] == "big"
