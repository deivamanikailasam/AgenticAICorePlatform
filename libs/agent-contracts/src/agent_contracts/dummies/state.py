from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Coroutine, Mapping, Sequence
from typing import Any

from agent_contracts.dummies._util import words
from agent_contracts.interfaces import Checkpointer, DurableExecutor, MemoryStore
from agent_contracts.models import (
    Checkpoint,
    MemoryItem,
    MemoryScope,
    RunContext,
    RunResult,
    RunState,
    RunStatus,
)


class InMemoryCheckpointer(Checkpointer):
    """Checkpoints in a dictionary keyed by (tenant, thread)."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], list[Checkpoint]] = {}

    async def put(self, checkpoint: Checkpoint) -> None:
        self._data.setdefault((checkpoint.tenant_id, checkpoint.thread_id), []).append(checkpoint)

    async def get(
        self, tenant_id: str, thread_id: str, checkpoint_id: str | None = None
    ) -> Checkpoint | None:
        items = self._data.get((tenant_id, thread_id), [])
        if checkpoint_id is None:
            return items[-1] if items else None
        return next((c for c in items if c.checkpoint_id == checkpoint_id), None)

    async def list(self, tenant_id: str, thread_id: str) -> Sequence[Checkpoint]:
        return list(self._data.get((tenant_id, thread_id), []))

    async def delete_thread(self, tenant_id: str, thread_id: str) -> int:
        return len(self._data.pop((tenant_id, thread_id), []))


class SqliteCheckpointer(Checkpointer):
    """Checkpoints in SQLite (in-memory by default). The 'SQLite (tests)' catalogue option."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "create table if not exists checkpoints (seq integer primary key autoincrement,"
            " tenant text, thread text, id text, body text)"
        )

    async def put(self, checkpoint: Checkpoint) -> None:
        self._db.execute(
            "insert into checkpoints (tenant, thread, id, body) values (?,?,?,?)",
            (
                checkpoint.tenant_id,
                checkpoint.thread_id,
                checkpoint.checkpoint_id,
                checkpoint.model_dump_json(),
            ),
        )
        self._db.commit()

    def _rows(self, tenant_id: str, thread_id: str) -> list[Checkpoint]:
        cur = self._db.execute(
            "select body from checkpoints where tenant=? and thread=? order by seq",
            (tenant_id, thread_id),
        )
        return [Checkpoint.model_validate_json(r[0]) for r in cur.fetchall()]

    async def get(
        self, tenant_id: str, thread_id: str, checkpoint_id: str | None = None
    ) -> Checkpoint | None:
        rows = self._rows(tenant_id, thread_id)
        if checkpoint_id is None:
            return rows[-1] if rows else None
        return next((c for c in rows if c.checkpoint_id == checkpoint_id), None)

    async def list(self, tenant_id: str, thread_id: str) -> Sequence[Checkpoint]:
        return self._rows(tenant_id, thread_id)

    async def delete_thread(self, tenant_id: str, thread_id: str) -> int:
        cur = self._db.execute(
            "delete from checkpoints where tenant=? and thread=?", (tenant_id, thread_id)
        )
        self._db.commit()
        return cur.rowcount


class WorkflowHandle:
    """What a durable workflow function receives: its inputs and a way to wait for signals."""

    def __init__(self, inputs: Mapping[str, Any]) -> None:
        self.inputs = dict(inputs)
        self._signals: dict[str, asyncio.Future[dict[str, Any]]] = {}

    def _future(self, name: str) -> asyncio.Future[dict[str, Any]]:
        if name not in self._signals:
            self._signals[name] = asyncio.get_running_loop().create_future()
        return self._signals[name]

    async def wait_signal(self, name: str) -> dict[str, Any]:
        return await self._future(name)

    def deliver(self, name: str, payload: Mapping[str, Any]) -> None:
        fut = self._future(name)
        if not fut.done():
            fut.set_result(dict(payload))


Workflow = Callable[[WorkflowHandle], Coroutine[Any, Any, Any]]


class _AsyncioDurable(DurableExecutor):
    def __init__(self, workflows: Mapping[str, Workflow] | None = None) -> None:
        self._workflows = dict(workflows or {})
        self._by_key: dict[str, str] = {}
        self._handles: dict[str, WorkflowHandle] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._pending: dict[str, tuple[str, RunContext]] = {}
        self._ctx: dict[str, RunContext] = {}
        self.starts = 0

    def _launch(self, run_id: str, workflow: str) -> None:
        self.starts += 1
        self._tasks[run_id] = asyncio.create_task(self._workflows[workflow](self._handles[run_id]))

    async def start(
        self, workflow: str, inputs: Mapping[str, Any], ctx: RunContext, idempotency_key: str
    ) -> str:
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        if workflow not in self._workflows:
            raise KeyError(workflow)
        run_id = ctx.run_id
        self._by_key[idempotency_key] = run_id
        self._handles[run_id] = WorkflowHandle(inputs)
        self._ctx[run_id] = ctx
        self._schedule(run_id, workflow)
        return run_id

    def _schedule(self, run_id: str, workflow: str) -> None:
        self._launch(run_id, workflow)

    def _ensure_started(self, run_id: str) -> None:
        if run_id in self._pending:
            workflow, _ = self._pending.pop(run_id)
            self._launch(run_id, workflow)

    async def signal(self, run_id: str, name: str, payload: Mapping[str, Any]) -> None:
        self._handles[run_id].deliver(name, payload)
        self._ensure_started(run_id)

    async def status(self, run_id: str) -> RunState:
        ctx = self._ctx[run_id]
        task = self._tasks.get(run_id)
        if task is None or not task.done():
            status = RunStatus.RUNNING
        elif task.exception() is not None:
            status = RunStatus.FAILED
        else:
            status = RunStatus.SUCCEEDED
        return RunState(run_id=run_id, thread_id=ctx.thread_id, status=status)

    async def result(self, run_id: str, wait_seconds: float = 30.0) -> RunResult:
        self._ensure_started(run_id)
        task = self._tasks[run_id]
        output = await asyncio.wait_for(asyncio.shield(task), wait_seconds)
        return RunResult(
            run_id=run_id,
            status=RunStatus.SUCCEEDED,
            output=output,
            state=await self.status(run_id),
        )


class InlineDurableExecutor(_AsyncioDurable):
    """Starts the workflow as an asyncio task immediately. No persistence."""


class DeferredDurableExecutor(_AsyncioDurable):
    """Starts the workflow lazily, on the first signal or result request. No persistence."""

    def _schedule(self, run_id: str, workflow: str) -> None:
        self._pending[run_id] = (workflow, self._ctx[run_id])


def _visible(item: MemoryItem, scope: MemoryScope) -> bool:
    if item.scope.tenant_id != scope.tenant_id:
        return False
    if scope.user_id and item.scope.user_id not in (None, scope.user_id):
        return False
    return not (scope.agent_id and item.scope.agent_id not in (None, scope.agent_id))


def _score(query: str, item: MemoryItem) -> float:
    q = words(query)
    return len(q & words(item.content)) / len(q) if q else 0.0


class InMemoryMemoryStore(MemoryStore):
    """Memory items in a dictionary; keyword-overlap search."""

    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}

    async def put(self, item: MemoryItem) -> str:
        self._items[item.id] = item
        return item.id

    async def get(self, scope: MemoryScope, item_id: str) -> MemoryItem | None:
        item = self._items.get(item_id)
        return item if item and _visible(item, scope) else None

    async def search(self, scope: MemoryScope, query: str, k: int = 5) -> Sequence[MemoryItem]:
        scored = [
            i.model_copy(update={"score": _score(query, i)})
            for i in self._items.values()
            if _visible(i, scope)
        ]
        scored = [i for i in scored if (i.score or 0) > 0]
        return sorted(scored, key=lambda i: i.score or 0, reverse=True)[:k]

    async def delete(self, scope: MemoryScope, item_id: str) -> bool:
        item = self._items.get(item_id)
        if item and _visible(item, scope):
            del self._items[item_id]
            return True
        return False

    async def erase_subject(self, tenant_id: str, user_id: str) -> int:
        doomed = [
            k
            for k, i in self._items.items()
            if i.scope.tenant_id == tenant_id and i.scope.user_id == user_id
        ]
        for k in doomed:
            del self._items[k]
        return len(doomed)


class SqliteMemoryStore(MemoryStore):
    """Memory items in SQLite (in-memory by default); keyword-overlap search in Python."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "create table if not exists memory (id text primary key, tenant text, user text,"
            " body text)"
        )

    def _all(self, tenant_id: str) -> list[MemoryItem]:
        cur = self._db.execute("select body from memory where tenant=?", (tenant_id,))
        return [MemoryItem.model_validate_json(r[0]) for r in cur.fetchall()]

    async def put(self, item: MemoryItem) -> str:
        self._db.execute(
            "insert or replace into memory values (?,?,?,?)",
            (item.id, item.scope.tenant_id, item.scope.user_id, item.model_dump_json()),
        )
        self._db.commit()
        return item.id

    async def get(self, scope: MemoryScope, item_id: str) -> MemoryItem | None:
        return next(
            (i for i in self._all(scope.tenant_id) if i.id == item_id and _visible(i, scope)), None
        )

    async def search(self, scope: MemoryScope, query: str, k: int = 5) -> Sequence[MemoryItem]:
        hits = [
            i.model_copy(update={"score": _score(query, i)})
            for i in self._all(scope.tenant_id)
            if _visible(i, scope)
        ]
        hits = [i for i in hits if (i.score or 0) > 0]
        return sorted(hits, key=lambda i: (-(i.score or 0), i.created_at))[:k]

    async def delete(self, scope: MemoryScope, item_id: str) -> bool:
        if await self.get(scope, item_id) is None:
            return False
        self._db.execute("delete from memory where id=?", (item_id,))
        self._db.commit()
        return True

    async def erase_subject(self, tenant_id: str, user_id: str) -> int:
        cur = self._db.execute("delete from memory where tenant=? and user=?", (tenant_id, user_id))
        self._db.commit()
        return cur.rowcount
