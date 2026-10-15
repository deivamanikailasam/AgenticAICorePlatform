"""``a2a://`` tool refs (Step 24): only remote agents on the platform's A2A allowlist load."""

import pytest
from agent_api.assembly import Assembly
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_spec import load_spec_text
from agent_spec.platform import A2aAgent, load_platform
from agent_tools.a2a import A2aRemoteAgentTool

ASKER = """
spec_version: 1
name: ask-helper
version: 0.1.0
owner: agent-platform
description: Asks a partner agent.
risk_tier: low
autonomy: workflow
pattern: workflow
runtime: { adapter: langgraph, durability: memory }
budgets: { max_steps: 5, max_wall_seconds: 20 }
tools:
  - ref: a2a://pyd-helper
graph:
  nodes:
    - { id: ask, type: tool, tool: pyd_helper, config: { args: { message: $input.question } } }
"""

PEM = "-----BEGIN PUBLIC KEY-----\nMFkw\n-----END PUBLIC KEY-----\n"


async def test_an_unlisted_remote_agent_is_refused_when_the_run_is_built() -> None:
    assembly = Assembly(load_platform(), RulesPolicyDecisionPoint(), MemoryAuditSink())
    with pytest.raises(LookupError, match="not on the platform allowlist"):
        await assembly.registry(load_spec_text(ASKER))


async def test_a_listed_remote_agent_is_registered_under_its_name() -> None:
    platform = load_platform().model_copy(deep=True)
    platform.a2a.agents.append(
        A2aAgent(name="pyd-helper", url="http://helper.test", card_keys={"k": PEM})
    )
    registry = await Assembly(platform, RulesPolicyDecisionPoint(), MemoryAuditSink()).registry(
        load_spec_text(ASKER)
    )
    tool = await registry.get("pyd-helper")
    assert isinstance(tool, A2aRemoteAgentTool)
    assert tool.spec.ref == "a2a://pyd-helper"
