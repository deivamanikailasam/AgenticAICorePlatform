"""``audit_sink.postgres``: the hash-chained audit trail in Postgres (Step 31, migration 0008).

One chain for every service. An append takes a transaction-scoped advisory lock, reads the last
row's hash, and inserts the next row with ``hash = sha256(canonical(prev, seq, event))``, the same
rule as the in-memory and JSONL sinks. Rows can't be updated or deleted (triggers). Anyone who
gets around that (a database administrator, a restore from a doctored backup) is caught by
``verify``, which recomputes every hash from the stored event and names the first broken row.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import psycopg
from agent_contracts.interfaces import AuditSink
from agent_contracts.models import AuditEvent, AuditRecord
from psycopg.types.json import Jsonb

from agent_audit.verify import Verdict

GENESIS = "0" * 64
LOCK = 731_031  # advisory lock key of the audit chain


def chain_hash(prev_hash: str, seq: int, event_body: dict[str, Any]) -> str:
    body = json.dumps(
        {"prev": prev_hash, "seq": seq, "event": event_body},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


class PostgresAuditSink(AuditSink):
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def _connect(self) -> psycopg.AsyncConnection[Any]:
        return await psycopg.AsyncConnection.connect(self.dsn, autocommit=True)

    async def append(self, event: AuditEvent) -> AuditRecord:
        body = event.chained()
        async with await self._connect() as conn, conn.transaction():
            await conn.execute("select pg_advisory_xact_lock(%s)", (LOCK,))
            row = await (
                await conn.execute(
                    "select seq, hash from agent_audit.events order by seq desc limit 1"
                )
            ).fetchone()
            seq, prev = (int(row[0]) + 1, str(row[1])) if row else (0, GENESIS)
            digest = chain_hash(prev, seq, body)
            await conn.execute(
                "insert into agent_audit.events (seq, prev_hash, hash, type, tenant_id, run_id,"
                " agent_id, trace_id, at, event) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    seq,
                    prev,
                    digest,
                    event.type,
                    event.ctx.tenant_id,
                    event.ctx.run_id,
                    event.ctx.agent_id,
                    event.trace_id,
                    event.at,
                    Jsonb(body),
                ),
            )
        return AuditRecord(seq=seq, event=event, prev_hash=prev, hash=digest)

    async def read(self, run_id: str | None = None) -> Sequence[AuditRecord]:
        """A run's records (its own and its workers' child runs), or all records, in order."""
        async with await self._connect() as conn:
            if run_id is None:
                cur = await conn.execute(
                    "select seq, prev_hash, hash, event from agent_audit.events order by seq"
                )
            else:
                cur = await conn.execute(
                    "select seq, prev_hash, hash, event from agent_audit.events"
                    " where run_id = %s or run_id like %s order by seq",
                    (run_id, f"{run_id}/%"),
                )
            rows = await cur.fetchall()
        return [
            AuditRecord(
                seq=int(r[0]),
                prev_hash=str(r[1]),
                hash=str(r[2]),
                event=AuditEvent.model_validate(r[3]),
            )
            for r in rows
        ]

    async def check(self) -> Verdict:
        """Recompute the whole chain from the stored events."""
        prev, count = GENESIS, 0
        async with await self._connect() as conn:
            cur = conn.cursor(name="audit_verify")  # server-side: the chain may be long
            async with conn.transaction():
                await cur.execute(
                    "select seq, prev_hash, hash, event from agent_audit.events order by seq"
                )
                async for seq, prev_hash, digest, body in cur:
                    if seq != count:
                        return Verdict(
                            False, count, count, f"row {count} is missing (next is {seq})"
                        )
                    if prev_hash != prev:
                        return Verdict(
                            False, count, seq, "prev_hash does not link to the row before"
                        )
                    if digest != chain_hash(prev, seq, body):
                        return Verdict(False, count, seq, "hash does not match the event")
                    prev, count = digest, count + 1
        return Verdict(True, count)

    async def verify(self) -> bool:
        return (await self.check()).ok
