from typing import Any

import pytest
from agent_contracts.models import ExecutablePlan, NodeKind, PlanEdge, PlanNode
from agent_contracts.testing import WORKFLOW_FUNCTIONS, WorkflowRuntimeContract
from agent_runtime.adapters.agent_framework import AgentFrameworkAdapter


class TestAgentFrameworkAdapter(WorkflowRuntimeContract):
    def make(self, functions: dict[str, Any] | None = None) -> AgentFrameworkAdapter:
        return AgentFrameworkAdapter(functions={**WORKFLOW_FUNCTIONS, **(functions or {})})


async def test_conditional_edge_into_a_join_is_refused() -> None:
    plan = ExecutablePlan(
        name="bad",
        nodes=(
            PlanNode(id="a", kind=NodeKind.FUNCTION, config={"fn": "start"}),
            PlanNode(id="j", kind=NodeKind.JOIN),
        ),
        edges=(PlanEdge(source="a", target="j", condition="ok"),),
        entry="a",
        exits=("j",),
    )
    with pytest.raises(ValueError, match="unconditional"):
        await AgentFrameworkAdapter().build(plan)
