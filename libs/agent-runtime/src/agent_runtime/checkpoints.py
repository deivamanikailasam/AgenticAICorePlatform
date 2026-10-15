"""Durable checkpoints (Step 18): Postgres (default), Valkey (development only), and the pieces
every backend shares — field encryption, retention and time travel.

* **Encryption.** Sensitive top-level state fields (default: ``input``, ``values``, ``value``,
  ``error``, ``transcript``) are encrypted with AES-256-GCM before they are stored. The key comes
  from a secret reference resolved by the composition root (OpenBao in production, Step 29); the
  tenant, thread and checkpoint id are bound in as associated data, so a ciphertext copied to
  another row or tenant does not decrypt.
* **Retention.** ``prune(older_than, keep_last)`` removes checkpoints older than the retention
  period but always keeps the newest ``keep_last`` per thread, so a thread can still resume.
* **Time travel.** ``history`` lists a thread's checkpoints; ``fork_thread`` copies a thread up to a
  chosen checkpoint into a new thread, from which a run can continue differently.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import psycopg
from agent_contracts.interfaces import Checkpointer
from agent_contracts.models import Checkpoint, new_id, utcnow
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SENSITIVE = ("input", "values", "value", "error", "transcript")
_ENC = "$enc"


class FieldCipher:
    """AES-256-GCM over JSON values; ``{"$enc": "<nonce+ciphertext, base64>"}`` when stored."""

    def __init__(self, key: bytes, fields: Sequence[str] = SENSITIVE) -> None:
        if len(key) != 32:
            raise ValueError("checkpoint encryption needs a 32-byte key")
        self._aead = AESGCM(key)
        self.fields = tuple(fields)

    @classmethod
    def from_b64(cls, key_b64: str, fields: Sequence[str] = SENSITIVE) -> FieldCipher:
        return cls(base64.b64decode(key_b64), fields)

    @staticmethod
    def _aad(cp: Checkpoint) -> bytes:
        return f"{cp.tenant_id}|{cp.thread_id}|{cp.checkpoint_id}".encode()

    def seal_value(self, value: Any, context: str) -> str:
        """Any JSON value as one ciphertext bound to ``context`` (e.g. ``run|<id>|event|3``)."""
        nonce = os.urandom(12)
        plain = json.dumps(value, default=str).encode()
        return base64.b64encode(nonce + self._aead.encrypt(nonce, plain, context.encode())).decode()

    def open_value(self, sealed: str, context: str) -> Any:
        raw = base64.b64decode(sealed)
        return json.loads(self._aead.decrypt(raw[:12], raw[12:], context.encode()))

    def seal(self, cp: Checkpoint) -> dict[str, Any]:
        out = dict(cp.state)
        for name in self.fields:
            if name in out:
                nonce = os.urandom(12)
                plain = json.dumps(out[name], default=str).encode()
                sealed = nonce + self._aead.encrypt(
                    nonce, plain, self._aad(cp) + b"|" + name.encode()
                )
                out[name] = {_ENC: base64.b64encode(sealed).decode()}
        return out

    def open(self, cp: Checkpoint) -> Checkpoint:
        state = dict(cp.state)
        for name, value in state.items():
            if isinstance(value, dict) and set(value) == {_ENC}:
                raw = base64.b64decode(value[_ENC])
                plain = self._aead.decrypt(raw[:12], raw[12:], self._aad(cp) + b"|" + name.encode())
                state[name] = json.loads(plain)
        return cp.model_copy(update={"state": state})


class PostgresCheckpointer(Checkpointer):
    """``agent_runtime.checkpoints`` in the ``agent`` database (Project 0 Postgres)."""

    component_status = "implemented"

    def __init__(self, dsn: str, cipher: FieldCipher | None) -> None:
        self._dsn = dsn
        self.cipher = cipher

    async def _conn(self) -> psycopg.AsyncConnection[Any]:
        return await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)

    def _row(self, row: Sequence[Any]) -> Checkpoint:
        cp = Checkpoint(
            tenant_id=row[0],
            thread_id=row[1],
            checkpoint_id=row[2],
            parent_id=row[3],
            run_id=row[4],
            step=row[5],
            node_id=row[6],
            state=row[7] if isinstance(row[7], dict) else json.loads(row[7]),
            created_at=row[8],
        )
        return self.cipher.open(cp) if self.cipher else cp

    _COLUMNS = (
        "tenant_id, thread_id, checkpoint_id, parent_id, run_id, step, node_id, state, created_at"
    )

    async def put(self, checkpoint: Checkpoint) -> None:
        state = self.cipher.seal(checkpoint) if self.cipher else checkpoint.state
        async with await self._conn() as conn:
            await conn.execute(
                f"insert into agent_runtime.checkpoints ({self._COLUMNS})"  # noqa: S608 - fixed columns
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    checkpoint.tenant_id,
                    checkpoint.thread_id,
                    checkpoint.checkpoint_id,
                    checkpoint.parent_id,
                    checkpoint.run_id,
                    checkpoint.step,
                    checkpoint.node_id,
                    json.dumps(state, default=str),
                    checkpoint.created_at,
                ),
            )

    async def get(
        self, tenant_id: str, thread_id: str, checkpoint_id: str | None = None
    ) -> Checkpoint | None:
        query = (
            f"select {self._COLUMNS} from agent_runtime.checkpoints"  # noqa: S608 - fixed columns
            " where tenant_id = %s and thread_id = %s"
        )
        params: tuple[Any, ...] = (tenant_id, thread_id)
        if checkpoint_id is not None:
            query += " and checkpoint_id = %s"
            params += (checkpoint_id,)
        async with await self._conn() as conn:
            row = await (
                await conn.execute(query + " order by seq desc limit 1", params)
            ).fetchone()
        return self._row(row) if row else None

    async def list(self, tenant_id: str, thread_id: str) -> Sequence[Checkpoint]:
        async with await self._conn() as conn:
            rows = await (
                await conn.execute(
                    f"select {self._COLUMNS} from agent_runtime.checkpoints"  # noqa: S608
                    " where tenant_id = %s and thread_id = %s order by seq",
                    (tenant_id, thread_id),
                )
            ).fetchall()
        return [self._row(r) for r in rows]

    async def for_run(self, tenant_id: str, run_id: str) -> Sequence[Checkpoint]:
        async with await self._conn() as conn:
            rows = await (
                await conn.execute(
                    f"select {self._COLUMNS} from agent_runtime.checkpoints"  # noqa: S608
                    " where tenant_id = %s and run_id = %s order by seq",
                    (tenant_id, run_id),
                )
            ).fetchall()
        return [self._row(r) for r in rows]

    async def delete_thread(self, tenant_id: str, thread_id: str) -> int:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "delete from agent_runtime.checkpoints where tenant_id = %s and thread_id = %s",
                (tenant_id, thread_id),
            )
            return cur.rowcount

    async def prune(self, older_than: timedelta, keep_last: int = 1) -> int:
        """Delete checkpoints older than ``older_than``, keeping each thread's newest ``keep_last``."""
        async with await self._conn() as conn:
            cur = await conn.execute(
                "delete from agent_runtime.checkpoints c using ("
                "  select seq, row_number() over (partition by tenant_id, thread_id order by seq desc) as rn"
                "  from agent_runtime.checkpoints) r"
                " where c.seq = r.seq and r.rn > %s and c.created_at < %s",
                (keep_last, utcnow() - older_than),
            )
            return cur.rowcount


class ValkeyCheckpointer(Checkpointer):
    """Development only: checkpoints in Valkey (one list per thread). No retention guarantees."""

    component_status = "implemented"

    def __init__(
        self, url: str, cipher: FieldCipher | None = None, prefix: str = "agent:cp:"
    ) -> None:
        import redis.asyncio as redis

        self._r = redis.from_url(url)
        self._prefix = prefix
        self.cipher = cipher

    def _key(self, tenant_id: str, thread_id: str) -> str:
        return f"{self._prefix}{tenant_id}:{thread_id}"

    async def put(self, checkpoint: Checkpoint) -> None:
        state = self.cipher.seal(checkpoint) if self.cipher else checkpoint.state
        body = checkpoint.model_copy(update={"state": state}).model_dump_json()
        await self._r.rpush(self._key(checkpoint.tenant_id, checkpoint.thread_id), body)

    async def list(self, tenant_id: str, thread_id: str) -> Sequence[Checkpoint]:
        raw = await self._r.lrange(self._key(tenant_id, thread_id), 0, -1)
        cps = [Checkpoint.model_validate_json(r) for r in raw]
        return [self.cipher.open(c) if self.cipher else c for c in cps]

    async def get(
        self, tenant_id: str, thread_id: str, checkpoint_id: str | None = None
    ) -> Checkpoint | None:
        rows = await self.list(tenant_id, thread_id)
        if checkpoint_id is None:
            return rows[-1] if rows else None
        return next((c for c in rows if c.checkpoint_id == checkpoint_id), None)

    async def delete_thread(self, tenant_id: str, thread_id: str) -> int:
        key = self._key(tenant_id, thread_id)
        n = int(await self._r.llen(key))
        await self._r.delete(key)
        return n

    async def aclose(self) -> None:
        await self._r.aclose()


async def history(cp: Checkpointer, tenant_id: str, thread_id: str) -> list[dict[str, Any]]:
    """Time travel, inspect: one line per checkpoint (no state), oldest first."""
    return [
        {
            "checkpoint_id": c.checkpoint_id,
            "parent_id": c.parent_id,
            "run_id": c.run_id,
            "step": c.step,
            "node_id": c.node_id,
            "created_at": c.created_at.isoformat(),
        }
        for c in await cp.list(tenant_id, thread_id)
    ]


async def fork_thread(
    cp: Checkpointer,
    tenant_id: str,
    thread_id: str,
    checkpoint_id: str,
    new_thread_id: str | None = None,
    new_run_id: str | None = None,
) -> tuple[str, str]:
    """Time travel, fork: copy ``thread_id`` up to and including ``checkpoint_id`` into a new
    thread (new ids, same parents in order) under a new run id. Returns ``(thread, run)``."""
    rows = list(await cp.list(tenant_id, thread_id))
    cut = next((i for i, c in enumerate(rows) if c.checkpoint_id == checkpoint_id), None)
    if cut is None:
        raise KeyError(f"no checkpoint {checkpoint_id} in thread {thread_id}")
    thread, run = new_thread_id or new_id(), new_run_id or new_id()
    parent: str | None = None
    for c in rows[: cut + 1]:
        copy = c.model_copy(
            update={
                "thread_id": thread,
                "checkpoint_id": new_id(),
                "parent_id": parent,
                "run_id": run,
                "created_at": datetime.now(c.created_at.tzinfo),
                "state": {**c.state, "forked_from": c.checkpoint_id},
            }
        )
        await cp.put(copy)
        parent = copy.checkpoint_id
    return thread, run
