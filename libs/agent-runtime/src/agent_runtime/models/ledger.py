"""Token and cost per model call, per run (the platform's side of cost accounting).

``UsageLedger`` keeps rows in memory. ``PostgresUsageLedger`` also writes them to
``agent_runtime.usage_ledger`` (migration 0006), so cost dashboards and ``just cost-reconcile``
can compare the platform's accounting with the gateway's (Step 26). Each row keeps the cost in
USD exactly as LiteLLM reported it (``x-litellm-response-cost``) next to the AUD conversion, and
the response id, which LiteLLM's spend logs record as ``request_id``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from agent_contracts.models import Usage


@dataclass(frozen=True)
class LedgerRow:
    run_id: str
    role: str
    alias: str
    usage: Usage
    cost_usd: float = 0.0
    call_id: str | None = None
    key: str | None = None  # whose gateway key paid for the call


@dataclass
class UsageLedger:
    """In memory, per run."""

    _rows: dict[str, list[LedgerRow]] = field(default_factory=lambda: defaultdict(list))

    def record(
        self,
        run_id: str,
        role: str,
        alias: str,
        usage: Usage,
        cost_usd: float = 0.0,
        call_id: str | None = None,
        key: str | None = None,
    ) -> None:
        self._rows[run_id].append(LedgerRow(run_id, role, alias, usage, cost_usd, call_id, key))

    async def flush(self) -> None:
        """Persist rows recorded since the last flush (nothing to do in memory)."""

    def rows(self, run_id: str) -> list[LedgerRow]:
        return list(self._rows.get(run_id, []))

    def total(self, run_id: str) -> Usage:
        total = Usage()
        for row in self._rows.get(run_id, []):
            total = total + row.usage
        return total

    def by_role(self, run_id: str) -> dict[str, Usage]:
        out: dict[str, Usage] = defaultdict(Usage)
        for row in self._rows.get(run_id, []):
            out[row.role] = out[row.role] + row.usage
        return dict(out)

    def calls(self, run_id: str) -> int:
        return len(self._rows.get(run_id, []))


class PostgresUsageLedger(UsageLedger):
    def __init__(self, dsn: str) -> None:
        super().__init__()
        self._dsn = dsn
        self._pending: list[LedgerRow] = []

    def record(
        self,
        run_id: str,
        role: str,
        alias: str,
        usage: Usage,
        cost_usd: float = 0.0,
        call_id: str | None = None,
        key: str | None = None,
    ) -> None:
        super().record(run_id, role, alias, usage, cost_usd, call_id, key)
        self._pending.append(self._rows[run_id][-1])

    async def flush(self) -> None:
        if not self._pending:
            return
        import psycopg

        rows, self._pending = self._pending, []
        params: list[tuple[Any, ...]] = [
            (
                r.run_id,
                r.role,
                r.alias,
                r.usage.input_tokens,
                r.usage.output_tokens,
                r.usage.cost_aud,
                r.cost_usd,
                r.call_id,
                r.key or r.role,
            )
            for r in rows
        ]
        conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        async with conn, conn.cursor() as cur:
            await cur.executemany(
                "insert into agent_runtime.usage_ledger (run_id, role, alias, input_tokens,"
                " output_tokens, cost_aud, cost_usd, call_id, key_name)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                params,
            )
