"""Serve every ``durability: temporal`` agent on a Temporal task queue.

The worker compiles each such spec, assembles its node runner exactly as the run API does (model
gateway, tools under policy and audit, functions) and registers the plan under the same handle
(``<name>@<version>``) that clients start runs with. Workflows only decide what runs next; every
model, tool and function call runs here, as the ``run_node`` activity.
"""

from __future__ import annotations

from collections.abc import Mapping

from agent_api.assembly import Assembly
from agent_compiler.compiler import compile_spec
from agent_runtime.temporal.runtime import TemporalRuntime
from agent_spec import AgentSpec


def temporal_agents(agents: Mapping[str, AgentSpec]) -> dict[str, AgentSpec]:
    return {n: s for n, s in agents.items() if s.runtime.durability.value == "temporal"}


async def serve(
    runtime: TemporalRuntime, assembly: Assembly, agents: Mapping[str, AgentSpec]
) -> list[str]:
    """Register each temporal agent's plan with ``runtime``; return their handles."""
    handles = []
    for spec in temporal_agents(agents).values():
        plan = compile_spec(spec)
        parts = await assembly.build(spec, plan)
        handle = await runtime.build(plan)
        runtime.activities.serve(handle, plan, parts.runner)
        handles.append(handle)
    return handles
