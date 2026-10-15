"""Agent-loop engines that cannot run in this process. They refuse clearly (ADR 0024)."""

from __future__ import annotations

from typing import Any

from agent_contracts.interfaces import AgentLoop
from agent_contracts.models import PlanNode, RunContext


class GoogleAdkLoopStub(AgentLoop):
    """Google ADK. STUB in-process: every ADK release up to 2.12.0 (2026-10-13) pins
    ``opentelemetry-sdk<=1.42.1``, which conflicts with the platform's telemetry pin. ADK agents
    join as **separate A2A services** with their own dependency lock (Step 21), where the
    platform calls them under the same policy and audit as any remote agent."""

    component_status = "stub"
    REASON = (
        "the google_adk agent loop runs as a separate A2A service (Step 21), not in-process: "
        "ADK pins opentelemetry-sdk<=1.42.1 (ADR 0024)"
    )

    def __init__(self, **_: Any) -> None:
        pass

    async def __call__(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> Any:
        raise NotImplementedError(self.REASON)
