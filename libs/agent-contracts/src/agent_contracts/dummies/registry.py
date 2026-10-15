"""Agent registries for tests and local use (Step 25): in memory, and a SQLite file."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from agent_contracts.interfaces import AgentRegistry
from agent_contracts.models import (
    AgentRecord,
    AgentStatus,
    AgentStatusChange,
    agent_version_key,
    check_agent_transition,
    utcnow,
)


def _sorted(records: list[AgentRecord]) -> list[AgentRecord]:
    return sorted(records, key=lambda r: (r.name, agent_version_key(r.version)))


class InMemoryAgentRegistry(AgentRegistry):
    """Records in a dictionary keyed by (name, version)."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], AgentRecord] = {}
        self._history: list[AgentStatusChange] = []

    async def register(self, record: AgentRecord) -> AgentRecord:
        key = (record.name, record.version)
        if key in self._records:
            stored = self._records[key]
            if stored.spec_hash != record.spec_hash:
                raise ValueError(
                    f"agent {record.name}@{record.version} is already registered with different "
                    "content; give the changed agent a new version"
                )
            return stored
        fresh = record.model_copy(
            update={"status": AgentStatus.DRAFT, "updated_at": utcnow(), "expires_at": None}
        )
        self._records[key] = fresh
        self._history.append(
            AgentStatusChange(
                name=record.name,
                version=record.version,
                from_status=None,
                to_status=AgentStatus.DRAFT,
                by=record.updated_by or "register",
            )
        )
        return fresh

    async def get(self, name: str, version: str | None = None) -> AgentRecord:
        if version is not None:
            return self._records[(name, version)]
        versions = [r for (n, _), r in self._records.items() if n == name]
        if not versions:
            raise KeyError(name)
        return _sorted(versions)[-1]

    async def list(self, include_retired: bool = False) -> Sequence[AgentRecord]:
        return _sorted(
            [
                r
                for r in self._records.values()
                if include_retired or r.status is not AgentStatus.RETIRED
            ]
        )

    async def set_status(
        self,
        name: str,
        version: str,
        status: AgentStatus,
        by: str,
        reason: str = "",
        expires_at: Any = None,
    ) -> AgentRecord:
        current = await self.get(name, version)
        check_agent_transition(current.status, status, by)
        updated = current.model_copy(
            update={
                "status": status,
                "status_reason": reason,
                "updated_by": by,
                "updated_at": utcnow(),
                "expires_at": expires_at if status is AgentStatus.APPROVED else None,
            }
        )
        self._records[(name, version)] = updated
        self._history.append(
            AgentStatusChange(
                name=name,
                version=version,
                from_status=current.status,
                to_status=status,
                by=by,
                reason=reason,
            )
        )
        return updated

    async def set_eval_status(self, name: str, version: str, eval_status: str) -> AgentRecord:
        current = await self.get(name, version)
        updated = current.model_copy(update={"eval_status": eval_status, "updated_at": utcnow()})
        self._records[(name, version)] = updated
        return updated

    async def history(self, name: str, version: str) -> Sequence[AgentStatusChange]:
        return [h for h in self._history if (h.name, h.version) == (name, version)]


class SqliteAgentRegistry(InMemoryAgentRegistry):
    """The same behaviour, persisted to a SQLite file (local development: ``just agents``
    without a database). Records are rewritten on every change."""

    def __init__(self, path: str = ":memory:") -> None:
        super().__init__()
        self._db = sqlite3.connect(path)
        self._db.execute(
            "create table if not exists agents (name text, version text, record text,"
            " primary key (name, version))"
        )
        self._db.execute(
            "create table if not exists agent_status_history (seq integer primary key,"
            " name text, version text, change text)"
        )
        for (raw,) in self._db.execute("select record from agents"):
            r = AgentRecord.model_validate_json(raw)
            self._records[(r.name, r.version)] = r
        for (raw,) in self._db.execute("select change from agent_status_history order by seq"):
            self._history.append(AgentStatusChange.model_validate_json(raw))

    def _save(self, record: AgentRecord) -> AgentRecord:
        key = (record.name, record.version)
        mine = [h for h in self._history if (h.name, h.version) == key]
        with self._db:
            self._db.execute(
                "insert or replace into agents (name, version, record) values (?, ?, ?)",
                (record.name, record.version, record.model_dump_json()),
            )
            (stored,) = self._db.execute(
                "select count(*) from agent_status_history where name = ? and version = ?", key
            ).fetchone()
            for change in mine[stored:]:
                self._db.execute(
                    "insert into agent_status_history (name, version, change) values (?, ?, ?)",
                    (*key, change.model_dump_json()),
                )
        return record

    async def register(self, record: AgentRecord) -> AgentRecord:
        return self._save(await super().register(record))

    async def set_status(
        self,
        name: str,
        version: str,
        status: AgentStatus,
        by: str,
        reason: str = "",
        expires_at: datetime | None = None,
    ) -> AgentRecord:
        return self._save(await super().set_status(name, version, status, by, reason, expires_at))

    async def set_eval_status(self, name: str, version: str, eval_status: str) -> AgentRecord:
        return self._save(await super().set_eval_status(name, version, eval_status))
