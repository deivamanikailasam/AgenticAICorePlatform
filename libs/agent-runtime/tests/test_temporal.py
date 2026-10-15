"""Step 19 without a server: the run_node activity (payload references, heartbeats) and the
documented Restate and DBOS stubs."""

from typing import Any

import pytest
from agent_contracts.models import ExecutablePlan, NodeKind, PlanEdge, PlanNode
from agent_contracts.testing import ctx
from agent_runtime.nodes import NodeRunner
from agent_runtime.temporal.activities import REF, NodeActivities
from agent_runtime.temporal.stubs import DbosDurableExecutor, RestateDurableExecutor
from temporalio.testing import ActivityEnvironment


class Artefacts:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> str:
        self.blobs[f"mem://{key}"] = data
        return f"mem://{key}"

    async def get(self, uri: str) -> bytes:
        return self.blobs[uri]


PLAN = ExecutablePlan(
    name="p",
    nodes=(
        PlanNode(id="big", kind=NodeKind.FUNCTION, config={"fn": "big"}),
        PlanNode(id="size", kind=NodeKind.FUNCTION, config={"fn": "size"}),
    ),
    edges=(PlanEdge(source="big", target="size"),),
    entry="big",
    exits=("size",),
)


async def test_large_values_travel_by_reference_and_are_resolved_for_the_next_node() -> None:
    store = Artefacts()
    acts = NodeActivities(artefacts=store, max_inline_bytes=100)
    runner = NodeRunner(
        {"big": lambda v: {"blob": "x" * 1000}, "size": lambda v: len(v["big"]["blob"])}
    )
    acts.serve("p@1", PLAN, runner)
    env = ActivityEnvironment()
    beats: list[Any] = []
    env.on_heartbeat = lambda *d: beats.append(d)
    c = ctx().model_dump(mode="json")
    first = await env.run(
        acts.run_node, {"handle": "p@1", "node_id": "big", "values": {"input": {}}, "ctx": c}
    )
    assert first["outcome"] == "ok"
    assert set(first["value"]) == {REF}, "1000 bytes is above the inline limit"
    assert beats, "the activity heartbeats while the node runs"
    second = await env.run(
        acts.run_node,
        {
            "handle": "p@1",
            "node_id": "size",
            "values": {"input": {}, "big": first["value"]},
            "ctx": c,
        },
    )
    assert second == {"outcome": "ok", "value": 1000, "error": None}


@pytest.mark.parametrize("stub", [RestateDurableExecutor, DbosDurableExecutor])
async def test_restate_and_dbos_are_documented_stubs(stub: Any) -> None:
    ex = stub()
    assert ex.component_status == "stub"
    with pytest.raises(NotImplementedError, match="documented stub \\(ADR 0030\\)"):
        await ex.start("double", {}, ctx(), idempotency_key="k")
