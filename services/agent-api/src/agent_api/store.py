"""Where the run API keeps runs and their AG-UI event logs (Step 18).

``MemoryRunStore`` for tests and single-process development; ``PostgresRunStore`` in the
``agent`` database, where each run's details (owner groups, input, pending approvals) and every
event are encrypted with the checkpoint key, bound to their run and position, and only the
columns needed to find a run (tenant, user, thread, agent, status, idempotency key) are in clear.
Access tokens are never stored.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Collection
from datetime import datetime
from typing import Any

import psycopg
from agent_runtime.checkpoints import FieldCipher


class RunStore(ABC):
    @abstractmethod
    async def save(self, record: dict[str, Any]) -> None:
        """Insert or update one run (``run_id`` is the key)."""

    @abstractmethod
    async def append(self, run_id: str, index: int, event: dict[str, Any]) -> None: ...

    @abstractmethod
    async def records(self) -> list[dict[str, Any]]:
        """Every stored run, oldest first."""

    @abstractmethod
    async def events(self, run_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def erase(self, tenant: str, user: str) -> int:
        """Right to erasure: delete the user's runs and their events; returns the run count."""

    @abstractmethod
    async def prune(self, before: datetime, statuses: Collection[str]) -> list[str]:
        """Retention (Step 37): delete runs in one of ``statuses`` last changed before
        ``before``, with their event logs; returns their ids. Runs that are still going or
        waiting for a person are never in ``statuses``."""


class MemoryRunStore(RunStore):
    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}

    async def save(self, record: dict[str, Any]) -> None:
        self._runs[record["run_id"]] = json.loads(json.dumps(record, default=str))

    async def append(self, run_id: str, index: int, event: dict[str, Any]) -> None:
        log = self._events.setdefault(run_id, [])
        assert index == len(log), "events are appended in order"
        log.append(event)

    async def records(self) -> list[dict[str, Any]]:
        return list(self._runs.values())

    async def events(self, run_id: str) -> list[dict[str, Any]]:
        return list(self._events.get(run_id, []))

    async def erase(self, tenant: str, user: str) -> int:
        doomed = [k for k, r in self._runs.items() if (r["tenant"], r["user"]) == (tenant, user)]
        for k in doomed:
            del self._runs[k]
            self._events.pop(k, None)
        return len(doomed)

    async def prune(self, before: datetime, statuses: Collection[str]) -> list[str]:
        cutoff = before.isoformat(timespec="seconds")
        doomed = [
            k
            for k, r in self._runs.items()
            if r["status"] in statuses and str(r.get("updated_at") or r["created_at"]) < cutoff
        ]
        for k in doomed:
            del self._runs[k]
            self._events.pop(k, None)
        return doomed


class PostgresRunStore(RunStore):
    INDEXED = (
        "run_id",
        "tenant",
        "user",
        "thread_id",
        "agent",
        "status",
        "idempotency_key",
        "body_hash",
    )

    def __init__(self, dsn: str, cipher: FieldCipher) -> None:
        self._dsn = dsn
        self.cipher = cipher

    async def _conn(self) -> psycopg.AsyncConnection[Any]:
        return await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)

    async def save(self, record: dict[str, Any]) -> None:
        doc = self.cipher.seal_value(record, f"run|{record['run_id']}")
        async with await self._conn() as conn:
            await conn.execute(
                "insert into agent_runtime.runs (run_id, tenant_id, user_id, thread_id, agent, status,"
                " idempotency_key, body_hash, doc) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " on conflict (run_id) do update set status = excluded.status, doc = excluded.doc,"
                " updated_at = now()",
                (
                    record["run_id"],
                    record["tenant"],
                    record["user"],
                    record["thread_id"],
                    record["agent"],
                    record["status"],
                    record.get("idempotency_key"),
                    record.get("body_hash"),
                    doc,
                ),
            )

    async def append(self, run_id: str, index: int, event: dict[str, Any]) -> None:
        sealed = self.cipher.seal_value(event, f"run|{run_id}|event|{index}")
        async with await self._conn() as conn:
            await conn.execute(
                "insert into agent_runtime.run_events (run_id, idx, event) values (%s, %s, %s)",
                (run_id, index, sealed),
            )

    async def records(self) -> list[dict[str, Any]]:
        async with await self._conn() as conn:
            rows = await (
                await conn.execute("select run_id, doc from agent_runtime.runs order by created_at")
            ).fetchall()
        return [dict(self.cipher.open_value(doc, f"run|{rid}")) for rid, doc in rows]

    async def events(self, run_id: str) -> list[dict[str, Any]]:
        async with await self._conn() as conn:
            rows = await (
                await conn.execute(
                    "select idx, event from agent_runtime.run_events where run_id = %s order by idx",
                    (run_id,),
                )
            ).fetchall()
        return [dict(self.cipher.open_value(e, f"run|{run_id}|event|{i}")) for i, e in rows]

    async def erase(self, tenant: str, user: str) -> int:
        async with await self._conn() as conn, conn.transaction():
            await conn.execute(
                "delete from agent_runtime.run_events where run_id in (select run_id from"
                " agent_runtime.runs where tenant_id = %s and user_id = %s)",
                (tenant, user),
            )
            cur = await conn.execute(
                "delete from agent_runtime.runs where tenant_id = %s and user_id = %s",
                (tenant, user),
            )
        return cur.rowcount

    async def prune(self, before: datetime, statuses: Collection[str]) -> list[str]:
        async with await self._conn() as conn, conn.transaction():
            rows = await (
                await conn.execute(
                    "delete from agent_runtime.runs where updated_at < %s and status = any(%s)"
                    " returning run_id",
                    (before, list(statuses)),
                )
            ).fetchall()
            ids = [r[0] for r in rows]
            if ids:
                await conn.execute(
                    "delete from agent_runtime.run_events where run_id = any(%s)", (ids,)
                )
        return ids
