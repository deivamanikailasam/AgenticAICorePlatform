"""Runtime adapters the catalogue lists as stubs. They refuse clearly instead of half-working."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, NoReturn

from agent_contracts.interfaces import RuntimeAdapter
from agent_contracts.models import ExecutablePlan, RunContext, RunEvent, RunResult, RunState


class CrewAIAdapter(RuntimeAdapter):
    """CrewAI. STUB (catalogue): role-based crews do not map onto the platform's plan graph
    without losing interrupts, typed state and budgets; see ADR 0022 for when to finish it."""

    component_status = "stub"
    REASON = "the crewai runtime adapter is a documented stub (ADR 0022)"

    def __init__(self, **_: Any) -> None:
        pass

    def _refuse(self) -> NoReturn:
        raise NotImplementedError(self.REASON)

    async def build(self, plan: ExecutablePlan) -> str:
        self._refuse()

    async def invoke(self, handle: str, inputs: Mapping[str, Any], ctx: RunContext) -> RunResult:
        self._refuse()

    def stream(
        self, handle: str, inputs: Mapping[str, Any], ctx: RunContext
    ) -> AsyncIterator[RunEvent]:
        self._refuse()

    async def resume(
        self, handle: str, run_id: str, payload: Mapping[str, Any], ctx: RunContext
    ) -> RunResult:
        self._refuse()

    async def cancel(self, run_id: str) -> None:
        raise KeyError(run_id)

    async def get_state(self, run_id: str) -> RunState:
        raise KeyError(run_id)
