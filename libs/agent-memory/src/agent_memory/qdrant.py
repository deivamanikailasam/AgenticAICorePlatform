"""``memory_store.qdrant``: memory in Project 0's Qdrant.

One collection per embedding dimension (``<prefix>_<dims>``, cosine). Every point carries its
scope, lifecycle and provenance as payload; every query filters on the caller's scope in Qdrant
itself, so another tenant's or user's points never leave the server. Empty scope fields are
stored as ``""`` and "never expires" as a far-future timestamp, so all filters are plain matches
and ranges. Content is encrypted at rest when a key is configured.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from agent_contracts.interfaces import Embedder, MemoryStore
from agent_contracts.models import MemoryItem, MemoryScope, Provenance
from agent_runtime.checkpoints import FieldCipher
from qdrant_client import AsyncQdrantClient, models

from agent_memory.common import Sealer, embed_ctx

NEVER = 32503680000.0  # 3000-01-01: "no expiry" as a number Qdrant can range over
KEYWORDS = ("tenant_id", "user_id", "agent_id", "subject_user_id", "key")


def point_id(item_id: str) -> str:
    try:
        return str(uuid.UUID(hex=item_id))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"agent-memory:{item_id}"))


def _match(field: str, value: Any) -> models.FieldCondition:
    return models.FieldCondition(key=field, match=models.MatchValue(value=value))


def visible(scope: MemoryScope) -> list[models.Condition]:
    now = datetime.now(UTC).timestamp()
    return [
        _match("tenant_id", scope.tenant_id),
        models.FieldCondition(
            key="user_id",
            match=models.MatchAny(any=["", *([scope.user_id] if scope.user_id else [])]),
        ),
        models.FieldCondition(
            key="agent_id",
            match=models.MatchAny(any=["", *([scope.agent_id] if scope.agent_id else [])]),
        ),
        models.FieldCondition(key="expires_at", range=models.Range(gt=now)),
    ]


class QdrantMemoryStore(MemoryStore):
    component_status = "implemented"

    def __init__(
        self,
        url: str = "http://127.0.0.1:6333",
        embedder: Embedder | None = None,
        cipher: FieldCipher | None = None,
        api_key: str | None = None,
        prefix: str = "agent_memory",
        client: AsyncQdrantClient | None = None,
    ) -> None:
        if embedder is None:
            from agent_runtime.models.embeddings import GatewayEmbedder

            embedder = GatewayEmbedder()
        self.client = client or AsyncQdrantClient(url=url, api_key=api_key)
        self.embedder = embedder
        self.sealer = Sealer(cipher)
        self.prefix = prefix
        self._ready: set[str] = set()

    async def _collection(self, dims: int) -> str:
        name = f"{self.prefix}_{dims}"
        if name in self._ready:
            return name
        if not await self.client.collection_exists(name):
            await self.client.create_collection(
                name,
                vectors_config=models.VectorParams(size=dims, distance=models.Distance.COSINE),
            )
            for field in KEYWORDS:
                await self.client.create_payload_index(
                    name, field, models.PayloadSchemaType.KEYWORD
                )
            await self.client.create_payload_index(
                name, "expires_at", models.PayloadSchemaType.FLOAT
            )
        self._ready.add(name)
        return name

    async def _collections(self) -> list[str]:
        found = await self.client.get_collections()
        return [c.name for c in found.collections if c.name.startswith(f"{self.prefix}_")]

    async def _vector(self, scope: MemoryScope, text: str) -> list[float]:
        (vec,) = await self.embedder.embed([text], embed_ctx(scope))
        return list(vec)

    def _payload(self, item: MemoryItem) -> dict[str, Any]:
        s = item.scope
        return {
            "item_id": item.id,
            "tenant_id": s.tenant_id,
            "user_id": s.user_id or "",
            "agent_id": s.agent_id or "",
            "subject_user_id": item.provenance.subject_user_id or "",
            "kind": item.kind.value,
            "key": item.key or "",
            "version": item.version,
            "content": self.sealer.seal(item),
            "provenance": item.provenance.model_dump(mode="json"),
            "confidence": item.confidence,
            "expires_at": item.expires_at.timestamp() if item.expires_at else NEVER,
            "created_at": item.created_at.isoformat(),
        }

    def _item(self, p: dict[str, Any], score: float | None = None) -> MemoryItem:
        exp = float(p["expires_at"])
        return MemoryItem(
            id=p["item_id"],
            scope=MemoryScope(
                tenant_id=p["tenant_id"],
                user_id=p["user_id"] or None,
                agent_id=p["agent_id"] or None,
            ),
            kind=p["kind"],
            key=p["key"] or None,
            version=p["version"],
            content=self.sealer.open(p["tenant_id"], p["item_id"], p["content"]),
            provenance=Provenance.model_validate(p["provenance"]),
            confidence=p["confidence"],
            expires_at=None if exp >= NEVER else datetime.fromtimestamp(exp, UTC),
            created_at=datetime.fromisoformat(p["created_at"]),
            score=score,
        )

    async def _versions(self, name: str, item: MemoryItem) -> list[int]:
        s = item.scope
        points, _ = await self.client.scroll(
            name,
            scroll_filter=models.Filter(
                must=[
                    _match("tenant_id", s.tenant_id),
                    _match("key", item.key or ""),
                    _match("user_id", s.user_id or ""),
                    _match("agent_id", s.agent_id or ""),
                ]
            ),
            limit=1000,
            with_payload=["version", "item_id"],
        )
        return [
            int((p.payload or {})["version"])
            for p in points
            if (p.payload or {}).get("item_id") != item.id
        ]

    async def put(self, item: MemoryItem) -> str:
        vec = await self._vector(item.scope, item.content)
        name = await self._collection(len(vec))
        if item.key:
            item = item.model_copy(
                update={"version": max(await self._versions(name, item), default=0) + 1}
            )
        await self.client.upsert(
            name,
            points=[
                models.PointStruct(id=point_id(item.id), vector=vec, payload=self._payload(item))
            ],
            wait=True,
        )
        return item.id

    async def get(self, scope: MemoryScope, item_id: str) -> MemoryItem | None:
        for name in await self._collections():
            points, _ = await self.client.scroll(
                name,
                scroll_filter=models.Filter(must=[*visible(scope), _match("item_id", item_id)]),
                limit=1,
            )
            if points:
                return self._item(points[0].payload or {})
        return None

    async def search(self, scope: MemoryScope, query: str, k: int = 5) -> Sequence[MemoryItem]:
        vec = await self._vector(scope, query)
        name = f"{self.prefix}_{len(vec)}"
        if not await self.client.collection_exists(name):
            return []
        found = await self.client.query_points(
            name, query=vec, query_filter=models.Filter(must=visible(scope)), limit=k * 4
        )
        hits = [self._item(p.payload or {}, p.score) for p in found.points]
        newest: dict[str, int] = {}
        for h in hits:
            if h.key and h.key not in newest:
                newest[h.key] = max([h.version, *await self._versions(name, h)])
        return [h for h in hits if not h.key or h.version == newest[h.key]][:k]

    async def delete(self, scope: MemoryScope, item_id: str) -> bool:
        if await self.get(scope, item_id) is None:
            return False
        for name in await self._collections():
            await self.client.delete(
                name, points_selector=models.PointIdsList(points=[point_id(item_id)]), wait=True
            )
        return True

    async def erase_subject(self, tenant_id: str, user_id: str) -> int:
        about = models.Filter(
            must=[_match("tenant_id", tenant_id)],
            should=[_match("user_id", user_id), _match("subject_user_id", user_id)],
        )
        total = 0
        for name in await self._collections():
            total += (await self.client.count(name, count_filter=about, exact=True)).count
            await self.client.delete(
                name, points_selector=models.FilterSelector(filter=about), wait=True
            )
        return total
