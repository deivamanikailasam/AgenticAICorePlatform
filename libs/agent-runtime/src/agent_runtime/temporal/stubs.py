"""Durable executors the catalogue lists as stubs (ADR 0030). They refuse clearly."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NoReturn

from agent_contracts.interfaces import DurableExecutor
from agent_contracts.models import RunContext, RunResult, RunState


class _Stub(DurableExecutor):
    component_status = "stub"
    NAME = ""

    def __init__(self, **_: Any) -> None:
        pass

    def _refuse(self) -> NoReturn:
        raise NotImplementedError(
            f"the {self.NAME} durable executor is a documented stub (ADR 0030); use temporal"
        )

    async def start(
        self, workflow: str, inputs: Mapping[str, Any], ctx: RunContext, idempotency_key: str
    ) -> str:
        self._refuse()

    async def signal(self, run_id: str, name: str, payload: Mapping[str, Any]) -> None:
        self._refuse()

    async def status(self, run_id: str) -> RunState:
        self._refuse()

    async def result(self, run_id: str, wait_seconds: float = 30.0) -> RunResult:
        self._refuse()


class RestateDurableExecutor(_Stub):
    """Restate. STUB: needs the Restate server and its SDK's handler model; finish when a team
    standardises on Restate (ADR 0030)."""

    NAME = "restate"


class DbosDurableExecutor(_Stub):
    """DBOS. STUB: durable steps in Postgres from a library; finish when Temporal's operational
    weight is not justified for a deployment (ADR 0030)."""

    NAME = "dbos"
