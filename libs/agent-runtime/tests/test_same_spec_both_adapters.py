"""Step 11 "Done when": the same agent.yaml passes the same tests on LangGraph and on the second
adapter (Microsoft Agent Framework) for the workflow pattern."""

from typing import Any

import pytest
from agent_compiler.compiler import compile_spec, runtime_for
from agent_compiler.run import functions_for
from agent_contracts.models import RunEventType, RunStatus
from agent_contracts.testing import ctx
from agent_spec import load_spec_text

F = "agent_contracts.testing.workflow"
SPEC = f"""
spec_version: 1
name: parallel-review
version: 0.1.0
owner: agent-platform
description: Fans out to two checks in parallel, joins, then routes by size.
risk_tier: low
autonomy: workflow
pattern: workflow
runtime: {{ adapter: ADAPTER, durability: memory }}
budgets: {{ max_steps: 20, max_wall_seconds: 30 }}
graph:
  entry: start
  nodes:
    - {{ id: start, type: function, fn: {F}.start }}
    - {{ id: b, type: function, fn: {F}.slow_b }}
    - {{ id: c, type: function, fn: {F}.slow_c }}
    - {{ id: both, type: join }}
    - {{ id: combine, type: function, fn: {F}.combine }}
    - {{ id: size, type: function, fn: {F}.size }}
    - {{ id: big, type: function, fn: {F}.combine }}
    - {{ id: small, type: function, fn: {F}.start }}
  edges:
    - "start -> b"
    - "start -> c"
    - "b -> both"
    - "c -> both"
    - "both -> combine"
    - "combine -> size"
    - "size -> big: big"
    - "size -> small: small"
"""

ADAPTERS = ["langgraph", "agent_framework"]


async def _run(adapter: str, inputs: dict[str, Any]) -> tuple[Any, list[str]]:
    spec = load_spec_text(SPEC.replace("ADAPTER", adapter))
    plan = compile_spec(spec)
    rt = runtime_for(spec, functions=functions_for(plan))
    handle = await rt.build(plan)
    c = ctx()
    events = [e async for e in rt.stream(handle, inputs, c)]
    state = await rt.get_state(c.run_id)
    assert state.status is RunStatus.SUCCEEDED, state
    finished = sorted(e.node_id or "" for e in events if e.type is RunEventType.NODE_FINISHED)
    return state.values, finished


@pytest.mark.parametrize("amount", [500, 5])
async def test_same_spec_same_result_on_both_adapters(amount: int) -> None:
    results = {a: await _run(a, {"amount": amount}) for a in ADAPTERS}
    (lg_values, lg_nodes), (af_values, af_nodes) = results.values()
    assert lg_values == af_values
    assert lg_nodes == af_nodes
    assert lg_values["combine"] == "A+B+C"
    assert ("big" in lg_values) is (amount > 100)
