"""The registry gate for this process (Step 25): the API and the worker both enforce it, so a
team cannot delegate to an unapproved agent in staging or prod however it is run."""

from __future__ import annotations

from collections.abc import Mapping

from agent_contracts.interfaces import AuditSink
from agent_registry.factory import open_registry
from agent_registry.gate import ApprovedOnly
from agent_registry.sync import sync
from agent_spec import AgentSpec


async def registry_gate(
    environment: str,
    audit: AuditSink,
    agents: Mapping[str, AgentSpec] | None = None,
    who: str = "agent-api",
) -> ApprovedOnly:
    """Open the configured registry; with ``agents``, register any new spec versions as draft."""
    registry, where = open_registry()
    for result in await sync(registry, (agents or {}).values(), by=who):
        if result.outcome.startswith("refused"):
            print(f"registry: {result.name}@{result.version} {result.outcome}", flush=True)
    gate = ApprovedOnly(registry, environment, audit)
    rule = " (approved agents only)" if gate.enforced else ""
    print(f"{who}: registry {where}; environment {environment}{rule}", flush=True)
    return gate
