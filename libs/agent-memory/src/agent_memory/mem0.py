"""``memory_store.mem0``: memory through the Mem0 open-source library (no Mem0 cloud).

Mem0 stores and searches the items (its hybrid semantic + keyword search) in a vector store we
choose; pgvector in the ``agent`` database by default. Three things are switched off or replaced
on purpose:

* **Fact extraction** (``infer``): Mem0 would rewrite content with a model *after* the platform's
  write policy approved it, which reopens the poisoning path the policy closes (ASI06). Items are
  stored exactly as the policy returned them.
* **Embeddings** come from the platform's ``embedder`` slot (gateway role, key and budget), not
  from Mem0's own provider client.
* **Telemetry** to Mem0's servers is disabled, and its local change history is kept in memory only,
  so no copy of personal data outlives an erasure in a side file.

Mem0 has no tenant concept: its ``user_id`` carries the tenant, and scope, subject, kind, key,
version and expiry are metadata matched by filters in the vector store. Mem0 embeds the stored
text, so content is not encrypted by the application here; rely on database encryption at rest.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from agent_contracts.interfaces import Embedder, MemoryStore
from agent_contracts.models import MemoryItem, MemoryScope, Provenance

from agent_memory.common import embed_ctx

os.environ["MEM0_TELEMETRY"] = "False"  # before mem0 is imported: never phone home

from mem0 import AsyncMemory
from mem0.configs.base import MemoryConfig
from mem0.embeddings.base import EmbeddingBase

NEVER = 32503680000.0


class PlatformEmbedding(EmbeddingBase):  # type: ignore[misc]
    """Mem0's embedding interface over the platform's ``embedder`` slot. Mem0 calls it from a
    worker thread, so each call runs the async embedder on that thread's own event loop."""

    def __init__(self, embedder: Embedder) -> None:
        super().__init__()
        self.embedder = embedder
        self.scope = MemoryScope(tenant_id="memory")

    def embed(self, text: Any, memory_action: Any = None) -> list[float]:
        (vec,) = asyncio.run(self.embedder.embed([str(text)], embed_ctx(self.scope)))
        return list(vec)

    def embed_batch(self, texts: Sequence[Any], memory_action: Any = None) -> list[list[float]]:
        return asyncio.run(self.embedder.embed([str(t) for t in texts], embed_ctx(self.scope)))


class Mem0MemoryStore(MemoryStore):
    component_status = "implemented"

    def __init__(
        self,
        dsn: str,
        embedder: Embedder | None = None,
        dims: int = 256,
        collection: str = "mem0_memories",
    ) -> None:
        if embedder is None:
            from agent_runtime.models.embeddings import GatewayEmbedder

            embedder = GatewayEmbedder()
        config = MemoryConfig.model_validate(
            {
                "vector_store": {
                    "provider": "pgvector",
                    "config": {
                        "connection_string": dsn,
                        "collection_name": collection,
                        "embedding_model_dims": dims,
                        "hnsw": True,
                    },
                },
                # placeholders only: the embedder is replaced below and no model is ever called
                "embedder": {
                    "provider": "openai",
                    "config": {"api_key": "unused", "embedding_dims": dims},
                },
                "llm": {"provider": "openai", "config": {"api_key": "unused", "model": "unused"}},
                "history_db_path": ":memory:",
            }
        )
        self.memory = AsyncMemory(config)
        self.memory.embedding_model = PlatformEmbedding(embedder)

    @staticmethod
    def _meta(item: MemoryItem) -> dict[str, Any]:
        s = item.scope
        return {
            "item_id": item.id,
            "scope_user": s.user_id or "",
            "scope_agent": s.agent_id or "",
            "subject_user": item.provenance.subject_user_id or "",
            "kind": item.kind.value,
            "key": item.key or "",
            "version": item.version,
            "provenance": item.provenance.model_dump(mode="json"),
            "confidence": item.confidence,
            "expires_at": item.expires_at.timestamp() if item.expires_at else NEVER,
            "created": item.created_at.isoformat(),
        }

    @staticmethod
    def _item(tenant: str, row: dict[str, Any]) -> MemoryItem:
        m = row.get("metadata") or {}
        exp = float(m["expires_at"])
        return MemoryItem(
            id=m["item_id"],
            scope=MemoryScope(
                tenant_id=tenant, user_id=m["scope_user"] or None, agent_id=m["scope_agent"] or None
            ),
            kind=m["kind"],
            key=m["key"] or None,
            version=m["version"],
            content=row["memory"],
            provenance=Provenance.model_validate(m["provenance"]),
            confidence=m["confidence"],
            expires_at=None if exp >= NEVER else datetime.fromtimestamp(exp, UTC),
            created_at=datetime.fromisoformat(m["created"]),
            score=row.get("score"),
        )

    @staticmethod
    def _visible(scope: MemoryScope, **extra: Any) -> dict[str, Any]:
        return {
            "user_id": scope.tenant_id,
            "scope_user": {"in": ["", *([scope.user_id] if scope.user_id else [])]},
            "scope_agent": {"in": ["", *([scope.agent_id] if scope.agent_id else [])]},
            "expires_at": {"gt": datetime.now(UTC).timestamp()},
            **extra,
        }

    async def _rows(self, filters: dict[str, Any], limit: int = 1000) -> list[dict[str, Any]]:
        found = await self.memory.get_all(filters=filters, top_k=limit)
        return list(found.get("results", []))

    async def _versions(self, item: MemoryItem) -> list[int]:
        s = item.scope
        rows = await self._rows(
            {
                "user_id": s.tenant_id,
                "key": item.key or "",
                "scope_user": s.user_id or "",
                "scope_agent": s.agent_id or "",
            }
        )
        return [int(r["metadata"]["version"]) for r in rows if r["metadata"]["item_id"] != item.id]

    async def put(self, item: MemoryItem) -> str:
        if item.key:
            item = item.model_copy(
                update={"version": max(await self._versions(item), default=0) + 1}
            )
        await self.memory.add(
            item.content, user_id=item.scope.tenant_id, metadata=self._meta(item), infer=False
        )
        return item.id

    async def get(self, scope: MemoryScope, item_id: str) -> MemoryItem | None:
        rows = await self._rows(self._visible(scope, item_id=item_id), limit=1)
        return self._item(scope.tenant_id, rows[0]) if rows else None

    async def search(self, scope: MemoryScope, query: str, k: int = 5) -> Sequence[MemoryItem]:
        found = await self.memory.search(
            query, filters=self._visible(scope), top_k=k * 4, threshold=0.0
        )
        hits = [self._item(scope.tenant_id, r) for r in found.get("results", [])]
        newest: dict[str, int] = {}
        for h in hits:
            if h.key and h.key not in newest:
                newest[h.key] = max([h.version, *await self._versions(h)])
        hits = [h for h in hits if not h.key or h.version == newest[h.key]]
        return sorted(hits, key=lambda h: -(h.score or 0))[:k]

    async def delete(self, scope: MemoryScope, item_id: str) -> bool:
        rows = await self._rows(self._visible(scope, item_id=item_id), limit=1)
        for r in rows:
            await self.memory.delete(r["id"])
        return bool(rows)

    async def erase_subject(self, tenant_id: str, user_id: str) -> int:
        rows = await self._rows({"user_id": tenant_id, "scope_user": user_id})
        rows += await self._rows({"user_id": tenant_id, "subject_user": user_id})
        ids = {r["id"] for r in rows}
        for mem0_id in ids:
            await self.memory.delete(mem0_id)
        return len(ids)
