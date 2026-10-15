"""The default agent registry: Postgres (the ``agent`` database, schema ``agent_registry``).

Status changes take a row lock (``select ... for update``) so two people approving and
retiring the same version at once cannot both win; the history row is written in the same
transaction.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any

import psycopg
from agent_contracts.interfaces import AgentRegistry
from agent_contracts.models import (
    AgentRecord,
    AgentStatus,
    AgentStatusChange,
    agent_version_key,
    check_agent_transition,
    utcnow,
)


class PostgresAgentRegistry(AgentRegistry):
    def __init__(self, dsn: str, namespace: str = "default") -> None:
        self._dsn = dsn
        self.namespace = namespace

    async def _conn(self) -> psycopg.AsyncConnection[Any]:
        return await psycopg.AsyncConnection.connect(self._dsn)

    async def register(self, record: AgentRecord) -> AgentRecord:
        fresh = record.model_copy(
            update={"status": AgentStatus.DRAFT, "updated_at": utcnow(), "expires_at": None}
        )
        async with await self._conn() as conn, conn.transaction():
            cur = await conn.execute(
                "insert into agent_registry.agents"
                " (namespace, name, version, record, status, owner, spec_hash)"
                " values (%s, %s, %s, %s, 'draft', %s, %s) on conflict do nothing",
                (
                    self.namespace,
                    fresh.name,
                    fresh.version,
                    fresh.model_dump_json(),
                    fresh.owner,
                    fresh.spec_hash,
                ),
            )
            if cur.rowcount == 1:
                await self._log(
                    conn,
                    fresh.name,
                    fresh.version,
                    None,
                    AgentStatus.DRAFT,
                    record.updated_by or "register",
                    "",
                )
                return fresh
        stored = await self.get(record.name, record.version)
        if stored.spec_hash != record.spec_hash:
            raise ValueError(
                f"agent {record.name}@{record.version} is already registered with different "
                "content; give the changed agent a new version"
            )
        return stored

    async def _log(
        self,
        conn: psycopg.AsyncConnection[Any],
        name: str,
        version: str,
        before: AgentStatus | None,
        after: AgentStatus,
        by: str,
        reason: str,
    ) -> None:
        await conn.execute(
            "insert into agent_registry.status_history"
            " (namespace, name, version, from_status, to_status, changed_by, reason)"
            " values (%s, %s, %s, %s, %s, %s, %s)",
            (
                self.namespace,
                name,
                version,
                before.value if before else None,
                after.value,
                by,
                reason,
            ),
        )

    async def get(self, name: str, version: str | None = None) -> AgentRecord:
        async with await self._conn() as conn:
            if version is None:
                cur = await conn.execute(
                    "select record from agent_registry.agents where namespace = %s and name = %s",
                    (self.namespace, name),
                )
            else:
                cur = await conn.execute(
                    "select record from agent_registry.agents"
                    " where namespace = %s and name = %s and version = %s",
                    (self.namespace, name, version),
                )
            rows = [AgentRecord.model_validate(r[0]) for r in await cur.fetchall()]
        if not rows:
            raise KeyError(name if version is None else f"{name}@{version}")
        return max(rows, key=lambda r: agent_version_key(r.version))

    async def list(self, include_retired: bool = False) -> Sequence[AgentRecord]:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "select record from agent_registry.agents"
                " where namespace = %s and (%s or status <> 'retired')",
                (self.namespace, include_retired),
            )
            rows = [AgentRecord.model_validate(r[0]) for r in await cur.fetchall()]
        return sorted(rows, key=lambda r: (r.name, agent_version_key(r.version)))

    async def _update(
        self,
        name: str,
        version: str,
        change: Callable[[psycopg.AsyncConnection[Any], AgentRecord], Awaitable[AgentRecord]],
    ) -> AgentRecord:
        async with await self._conn() as conn, conn.transaction():
            cur = await conn.execute(
                "select record from agent_registry.agents"
                " where namespace = %s and name = %s and version = %s for update",
                (self.namespace, name, version),
            )
            row = await cur.fetchone()
            if row is None:
                raise KeyError(f"{name}@{version}")
            current = AgentRecord.model_validate(row[0])
            updated = await change(conn, current)
            await conn.execute(
                "update agent_registry.agents set record = %s, status = %s, updated_at = now()"
                " where namespace = %s and name = %s and version = %s",
                (updated.model_dump_json(), updated.status.value, self.namespace, name, version),
            )
            return updated

    async def set_status(
        self,
        name: str,
        version: str,
        status: AgentStatus,
        by: str,
        reason: str = "",
        expires_at: datetime | None = None,
    ) -> AgentRecord:
        async def change(conn: psycopg.AsyncConnection[Any], current: AgentRecord) -> AgentRecord:
            check_agent_transition(current.status, status, by)
            await self._log(conn, name, version, current.status, status, by, reason)
            return current.model_copy(
                update={
                    "status": status,
                    "status_reason": reason,
                    "updated_by": by,
                    "updated_at": utcnow(),
                    "expires_at": expires_at if status is AgentStatus.APPROVED else None,
                }
            )

        return await self._update(name, version, change)

    async def set_eval_status(self, name: str, version: str, eval_status: str) -> AgentRecord:
        async def change(_conn: psycopg.AsyncConnection[Any], current: AgentRecord) -> AgentRecord:
            return current.model_copy(update={"eval_status": eval_status, "updated_at": utcnow()})

        return await self._update(name, version, change)

    async def history(self, name: str, version: str) -> Sequence[AgentStatusChange]:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "select from_status, to_status, changed_by, reason, at"
                " from agent_registry.status_history"
                " where namespace = %s and name = %s and version = %s order by seq",
                (self.namespace, name, version),
            )
            return [
                AgentStatusChange(
                    name=name,
                    version=version,
                    from_status=AgentStatus(r[0]) if r[0] else None,
                    to_status=AgentStatus(r[1]),
                    by=r[2],
                    reason=r[3],
                    at=r[4],
                )
                for r in await cur.fetchall()
            ]
