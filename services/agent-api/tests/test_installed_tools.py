"""A team's own tools (Step 39): a ``local://`` ref loads the installed ``tool`` component that
declares exactly that ref; anything else is refused before the run starts."""

from typing import Any

import pytest
from agent_api.assembly import Assembly, installed_tool
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import Tool
from agent_contracts.models import RunContext, SideEffect, ToolSpec
from agent_contracts.registry import registry
from agent_spec import load_spec_text
from agent_spec.platform import load_platform


class Shout(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="shout",
            version="1.0.0",
            ref="local://team-a/shout",
            description="Upper-cases text.",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            side_effect=SideEffect.READ,
        )

    async def invoke(self, arguments: Any, ctx: RunContext) -> Any:
        return str(arguments["text"]).upper()


SPEC = """
spec_version: 1
name: shouter
version: 0.1.0
owner: team-a
risk_tier: low
autonomy: workflow
pattern: workflow
runtime: { adapter: langgraph, durability: memory }
budgets: { max_steps: 5, max_wall_seconds: 20 }
tools:
  - ref: REF
graph:
  nodes:
    - { id: shout, type: tool, tool: shout, config: { args: { text: $input.text } } }
"""


@pytest.fixture(autouse=True)
def _shout() -> Any:
    registry.add("tool", "team-a.shout", Shout)
    yield
    del registry._classes[("tool", "team-a.shout")]


async def test_an_installed_tool_loads_by_its_exact_ref() -> None:
    assembly = Assembly(load_platform(), RulesPolicyDecisionPoint(), MemoryAuditSink())
    reg = await assembly.registry(load_spec_text(SPEC.replace("REF", "local://team-a/shout")))
    assert isinstance(await reg.get("shout"), Shout)


async def test_a_ref_no_installed_tool_declares_is_refused() -> None:
    assert installed_tool("local://team-b/shout") is None
    assembly = Assembly(load_platform(), RulesPolicyDecisionPoint(), MemoryAuditSink())
    with pytest.raises(LookupError, match="not available through the run API"):
        await assembly.registry(load_spec_text(SPEC.replace("REF", "local://team-b/shout")))
