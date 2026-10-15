"""``approval_gate.postgres`` (Step 28): approval requests and decisions in the agent database.

A decision takes a row lock, so two reviewers deciding at once cannot both win; an expired
request (past ``expires_at``) reads as EXPIRED and cannot be decided.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import psycopg
from agent_contracts.interfaces import ApprovalGate
from agent_contracts.models import ApprovalDecision, ApprovalRecord, ApprovalRequest, ApprovalStatus


def _fresh(record: ApprovalRecord) -> ApprovalRecord:
    exp = record.request.expires_at
    if record.status is ApprovalStatus.PENDING and exp is not None and exp <= datetime.now(UTC):
        return record.model_copy(update={"status": ApprovalStatus.EXPIRED})
    return record


class PostgresApprovalGate(ApprovalGate):
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def _conn(self) -> psycopg.AsyncConnection[Any]:
        return await psycopg.AsyncConnection.connect(self._dsn)

    async def request(self, request: ApprovalRequest) -> ApprovalRecord:
        record = ApprovalRecord(request=request, status=ApprovalStatus.PENDING)
        async with await self._conn() as conn, conn.transaction():
            await conn.execute(
                "insert into agent_runtime.approvals (id, tenant_id, run_id, approvers, status,"
                " record, expires_at) values (%s, %s, %s, %s, 'pending', %s, %s)"
                " on conflict (id) do nothing",
                (
                    request.id,
                    request.ctx.tenant_id,
                    request.ctx.run_id,
                    request.approvers,
                    record.model_dump_json(),
                    request.expires_at,
                ),
            )
        return await self.get(request.id)

    async def get(self, approval_id: str) -> ApprovalRecord:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "select record from agent_runtime.approvals where id = %s", (approval_id,)
            )
            row = await cur.fetchone()
        if row is None:
            raise KeyError(approval_id)
        return _fresh(ApprovalRecord.model_validate(row[0]))

    async def decide(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRecord:
        async with await self._conn() as conn, conn.transaction():
            cur = await conn.execute(
                "select record from agent_runtime.approvals where id = %s for update",
                (approval_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise KeyError(approval_id)
            record = _fresh(ApprovalRecord.model_validate(row[0]))
            if record.status is not ApprovalStatus.PENDING:
                raise ValueError(f"approval {approval_id} is {record.status.value}")
            now = datetime.now(UTC)
            done = record.model_copy(
                update={"status": decision.status, "decision": decision, "decided_at": now}
            )
            await conn.execute(
                "update agent_runtime.approvals set status = %s, record = %s, decided_at = %s"
                " where id = %s",
                (decision.status.value, done.model_dump_json(), now, approval_id),
            )
        return done

    async def pending(self, approvers: str | None = None) -> Sequence[ApprovalRecord]:
        async with await self._conn() as conn:
            if approvers is None:
                cur = await conn.execute(
                    "select record from agent_runtime.approvals where status = 'pending'"
                    " order by created_at"
                )
            else:
                cur = await conn.execute(
                    "select record from agent_runtime.approvals where status = 'pending'"
                    " and approvers = %s order by created_at",
                    (approvers,),
                )
            rows = [_fresh(ApprovalRecord.model_validate(r[0])) for r in await cur.fetchall()]
        return [r for r in rows if r.status is ApprovalStatus.PENDING]
