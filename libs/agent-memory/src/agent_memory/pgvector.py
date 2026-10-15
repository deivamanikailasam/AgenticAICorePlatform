"""``memory_store.pgvector`` (default): memory in the ``agent`` database with pgvector.

Search is cosine similarity over the item's embedding, inside the caller's scope only, skipping
expired items and older versions of procedural memory. Content is encrypted at rest when a key is
configured. Exact search (no ANN index) is used because the column accepts any embedding
dimension; add an HNSW index on ``embedding::vector(<dims>)`` once the embedding model is fixed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import psycopg
from agent_contracts.interfaces import Embedder, MemoryStore
from agent_contracts.models import MemoryItem, MemoryScope, Provenance
from agent_runtime.checkpoints import FieldCipher
from psycopg import sql

from agent_memory.common import Sealer, embed_ctx

COLUMNS = (
    "id, tenant_id, user_id, agent_id, kind, key, version, content, provenance, confidence,"
    " expires_at, created_at"
)
VISIBLE = (
    "tenant_id = %(t)s and (user_id is null or user_id = %(u)s)"
    " and (agent_id is null or agent_id = %(a)s) and (expires_at is null or expires_at > now())"
)
NEWEST = (
    "(key is null or version = (select max(n.version) from agent_memory.items n"
    " where n.tenant_id = i.tenant_id and n.key = i.key"
    " and n.user_id is not distinct from i.user_id and n.agent_id is not distinct from i.agent_id))"
)


# Fixed query text only: every value goes in as a bound parameter.
GET = sql.SQL("select {} from agent_memory.items where id = %(id)s and {}").format(
    sql.SQL(COLUMNS), sql.SQL(VISIBLE)
)
SEARCH = sql.SQL(
    "select {}, 1 - (embedding <=> %(q)s::vector) from agent_memory.items i"
    " where {} and vector_dims(embedding) = %(d)s and {}"
    " order by embedding <=> %(q)s::vector, created_at limit %(k)s"
).format(sql.SQL(COLUMNS), sql.SQL(VISIBLE), sql.SQL(NEWEST))
ITEMS = sql.SQL(
    "select {} from agent_memory.items where {} order by created_at desc limit %(n)s"
).format(sql.SQL(COLUMNS), sql.SQL(VISIBLE))
DELETE = sql.SQL("delete from agent_memory.items where id = %(id)s and {}").format(sql.SQL(VISIBLE))


def vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


class PgVectorMemoryStore(MemoryStore):
    component_status = "implemented"

    def __init__(
        self,
        dsn: str,
        embedder: Embedder | None = None,
        cipher: FieldCipher | None = None,
    ) -> None:
        if embedder is None:
            from agent_runtime.models.embeddings import GatewayEmbedder

            embedder = GatewayEmbedder()
        self._dsn = dsn
        self.embedder = embedder
        self.sealer = Sealer(cipher)

    async def _conn(self) -> psycopg.AsyncConnection[Any]:
        return await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)

    async def _vector(self, scope: MemoryScope, text: str) -> list[float]:
        (vec,) = await self.embedder.embed([text], embed_ctx(scope))
        return vec

    def _row(self, row: Sequence[Any], score: float | None = None) -> MemoryItem:
        rid, tenant, user, agent, kind, key, version, content, prov, conf, exp, created = row[:12]
        return MemoryItem(
            id=rid,
            scope=MemoryScope(tenant_id=tenant, user_id=user, agent_id=agent),
            kind=kind,
            key=key,
            version=version,
            content=self.sealer.open(tenant, rid, content),
            provenance=Provenance.model_validate(prov),
            confidence=conf,
            expires_at=exp,
            created_at=created,
            score=score,
        )

    async def put(self, item: MemoryItem) -> str:
        vec = vector_literal(await self._vector(item.scope, item.content))
        s = item.scope
        async with await self._conn() as conn, conn.transaction():
            version = 1
            if item.key:
                await conn.execute(
                    "select pg_advisory_xact_lock(hashtext(%s))", (f"{s.tenant_id}|{item.key}",)
                )
                cur = await conn.execute(
                    "select coalesce(max(version), 0) + 1 from agent_memory.items where"
                    " tenant_id = %s and key = %s and user_id is not distinct from %s"
                    " and agent_id is not distinct from %s and id <> %s",
                    (s.tenant_id, item.key, s.user_id, s.agent_id, item.id),
                )
                version = int((await cur.fetchone() or (1,))[0])
            await conn.execute(
                "insert into agent_memory.items (id, tenant_id, user_id, agent_id, subject_user_id,"
                " kind, key, version, content, provenance, confidence, expires_at, created_at,"
                " embedding) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s::vector) on conflict (id) do update set content = excluded.content,"
                " provenance = excluded.provenance, confidence = excluded.confidence,"
                " expires_at = excluded.expires_at, embedding = excluded.embedding",
                (
                    item.id,
                    s.tenant_id,
                    s.user_id,
                    s.agent_id,
                    item.provenance.subject_user_id,
                    item.kind.value,
                    item.key,
                    version,
                    self.sealer.seal(item),
                    json.dumps(item.provenance.model_dump(mode="json")),
                    item.confidence,
                    item.expires_at,
                    item.created_at,
                    vec,
                ),
            )
        return item.id

    @staticmethod
    def _params(scope: MemoryScope, **extra: Any) -> dict[str, Any]:
        return {"t": scope.tenant_id, "u": scope.user_id, "a": scope.agent_id, **extra}

    async def get(self, scope: MemoryScope, item_id: str) -> MemoryItem | None:
        async with await self._conn() as conn:
            cur = await conn.execute(GET, self._params(scope, id=item_id))
            row = await cur.fetchone()
        return self._row(row) if row else None

    async def search(self, scope: MemoryScope, query: str, k: int = 5) -> Sequence[MemoryItem]:
        vec = await self._vector(scope, query)
        async with await self._conn() as conn:
            cur = await conn.execute(
                SEARCH,
                self._params(scope, q=vector_literal(vec), d=len(vec), k=k),
            )
            rows = await cur.fetchall()
        return [self._row(r, float(r[12])) for r in rows]

    async def delete(self, scope: MemoryScope, item_id: str) -> bool:
        async with await self._conn() as conn:
            cur = await conn.execute(DELETE, self._params(scope, id=item_id))
        return cur.rowcount > 0

    async def erase_subject(self, tenant_id: str, user_id: str) -> int:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "delete from agent_memory.items where tenant_id = %s"
                " and (user_id = %s or subject_user_id = %s)",
                (tenant_id, user_id, user_id),
            )
        return cur.rowcount

    async def items(self, scope: MemoryScope, limit: int = 100) -> Sequence[MemoryItem]:
        async with await self._conn() as conn:
            cur = await conn.execute(ITEMS, self._params(scope, n=limit))
            rows = await cur.fetchall()
        return [self._row(r) for r in rows]

    async def flush_run(self, tenant_id: str, run_id: str) -> int:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "delete from agent_memory.items where tenant_id = %s"
                " and provenance->>'run_id' = %s",
                (tenant_id, run_id),
            )
        return cur.rowcount

    async def purge_expired(self) -> int:
        """Delete expired items (run from a scheduled job; search already hides them)."""
        async with await self._conn() as conn:
            cur = await conn.execute("delete from agent_memory.items where expires_at <= now()")
        return cur.rowcount
