"""Where the run API keeps runs and their AG-UI event logs (Step 18).

``MemoryRunStore`` for tests and single-process development; ``PostgresRunStore`` in the
``agent`` database, where each run's details (owner groups, input, pending approvals) and every
event are encrypted with the checkpoint key, bound to their run and position, and only the
columns needed to find a run (tenant, user, thread, agent, status, idempotency key) are in clear.
Access tokens are never stored.

**Leases (Step 38).** With several API replicas sharing the store, each unfinished run is driven
by one replica at a time: the one holding its lease (``claim``, kept alive by ``renew``). When a
replica stops renewing, its leases lapse and another replica claims the run and continues it from
its checkpoints. Finished runs need no lease; any replica can read them.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import Collection
from datetime import datetime
from typing import Any

import psycopg
from agent_runtime.checkpoints import FieldCipher


class IdempotencyRaceError(Exception):
    """Another replica stored a run with the same idempotency key first."""


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

    @abstractmethod
    async def record(self, run_id: str) -> dict[str, Any] | None:
        """One stored run, or None."""

    @abstractmethod
    async def find_idempotent(self, tenant: str, user: str, key: str) -> dict[str, Any] | None:
        """The run this user created with this idempotency key, or None."""

    @abstractmethod
    async def claim(self, run_id: str, node: str, seconds: float) -> str | None:
        """Take the run's lease for ``node`` if nobody else holds a live one; return who holds
        it afterwards (None when the run does not exist)."""

    @abstractmethod
    async def holder(self, run_id: str) -> str | None:
        """The replica holding a live lease on the run, if any."""

    @abstractmethod
    async def renew(self, node: str, run_ids: Collection[str], seconds: float) -> set[str]:
        """Extend ``node``'s leases; returns the runs it still holds."""

    @abstractmethod
    async def orphans(self, statuses: Collection[str]) -> list[str]:
        """Runs in one of ``statuses`` that no replica holds a live lease on."""


class MemoryRunStore(RunStore):
    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._leases: dict[str, tuple[str, float]] = {}  # run -> (node, expires, monotonic)

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

    async def record(self, run_id: str) -> dict[str, Any] | None:
        rec = self._runs.get(run_id)
        return dict(rec) if rec is not None else None

    async def find_idempotent(self, tenant: str, user: str, key: str) -> dict[str, Any] | None:
        return next(
            (
                dict(r)
                for r in self._runs.values()
                if (r["tenant"], r["user"], r.get("idempotency_key")) == (tenant, user, key)
            ),
            None,
        )

    async def claim(self, run_id: str, node: str, seconds: float) -> str | None:
        if run_id not in self._runs:
            return None
        held = self._leases.get(run_id)
        if held is None or held[0] == node or held[1] < time.monotonic():
            self._leases[run_id] = (node, time.monotonic() + seconds)
        return self._leases[run_id][0]

    async def holder(self, run_id: str) -> str | None:
        held = self._leases.get(run_id)
        return held[0] if held is not None and held[1] >= time.monotonic() else None

    async def renew(self, node: str, run_ids: Collection[str], seconds: float) -> set[str]:
        kept = {r for r in run_ids if self._leases.get(r, ("", 0.0))[0] == node}
        for r in kept:
            self._leases[r] = (node, time.monotonic() + seconds)
        return kept

    async def orphans(self, statuses: Collection[str]) -> list[str]:
        now = time.monotonic()
        return [
            k
            for k, r in self._runs.items()
            if r["status"] in statuses and self._leases.get(k, ("", 0.0))[1] < now
        ]


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
            try:
                await self._upsert(conn, record, doc)
            except psycopg.errors.UniqueViolation as exc:  # same key, another replica first
                raise IdempotencyRaceError(record.get("idempotency_key")) from exc

    async def _upsert(
        self, conn: psycopg.AsyncConnection[Any], record: dict[str, Any], doc: str
    ) -> None:
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

    async def record(self, run_id: str) -> dict[str, Any] | None:
        async with await self._conn() as conn:
            row = await (
                await conn.execute(
                    "select doc from agent_runtime.runs where run_id = %s", (run_id,)
                )
            ).fetchone()
        return dict(self.cipher.open_value(row[0], f"run|{run_id}")) if row else None

    async def find_idempotent(self, tenant: str, user: str, key: str) -> dict[str, Any] | None:
        async with await self._conn() as conn:
            row = await (
                await conn.execute(
                    "select run_id, doc from agent_runtime.runs where tenant_id = %s and"
                    " user_id = %s and idempotency_key = %s",
                    (tenant, user, key),
                )
            ).fetchone()
        return dict(self.cipher.open_value(row[1], f"run|{row[0]}")) if row else None

    async def claim(self, run_id: str, node: str, seconds: float) -> str | None:
        async with await self._conn() as conn:
            row = await (
                await conn.execute(
                    "update agent_runtime.runs set lease_owner = %s,"
                    " lease_until = now() + make_interval(secs => %s)"
                    " where run_id = %s and (lease_owner is null or lease_owner = %s"
                    " or lease_until < now()) returning lease_owner",
                    (node, seconds, run_id, node),
                )
            ).fetchone()
            if row is None:
                row = await (
                    await conn.execute(
                        "select lease_owner from agent_runtime.runs where run_id = %s", (run_id,)
                    )
                ).fetchone()
        return str(row[0]) if row and row[0] else None

    async def holder(self, run_id: str) -> str | None:
        async with await self._conn() as conn:
            row = await (
                await conn.execute(
                    "select lease_owner from agent_runtime.runs where run_id = %s"
                    " and lease_until >= now()",
                    (run_id,),
                )
            ).fetchone()
        return str(row[0]) if row and row[0] else None

    async def renew(self, node: str, run_ids: Collection[str], seconds: float) -> set[str]:
        if not run_ids:
            return set()
        async with await self._conn() as conn:
            rows = await (
                await conn.execute(
                    "update agent_runtime.runs set lease_until = now() + make_interval(secs => %s)"
                    " where run_id = any(%s) and lease_owner = %s returning run_id",
                    (seconds, list(run_ids), node),
                )
            ).fetchall()
        return {str(r[0]) for r in rows}

    async def orphans(self, statuses: Collection[str]) -> list[str]:
        async with await self._conn() as conn:
            rows = await (
                await conn.execute(
                    "select run_id from agent_runtime.runs where status = any(%s)"
                    " and (lease_until is null or lease_until < now()) order by created_at",
                    (list(statuses),),
                )
            ).fetchall()
        return [str(r[0]) for r in rows]
