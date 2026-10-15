"""Shared by every store: the embedding context, the item row format and encryption."""

from __future__ import annotations

from typing import Any

from agent_contracts.models import MemoryItem, MemoryScope, RunContext
from agent_runtime.checkpoints import FieldCipher


def embed_ctx(scope: MemoryScope) -> RunContext:
    """Embedding calls are attributed to the tenant and user the memory belongs to."""
    return RunContext(
        tenant_id=scope.tenant_id,
        user_id=scope.user_id or "memory",
        agent_id=scope.agent_id or "memory",
    )


class Sealer:
    """Encrypts memory content bound to its tenant and id; a no-op without a key (tests)."""

    def __init__(self, cipher: FieldCipher | None) -> None:
        self.cipher = cipher

    def seal(self, item: MemoryItem) -> str:
        if self.cipher is None:
            return item.content
        return self.cipher.seal_value(item.content, f"memory|{item.scope.tenant_id}|{item.id}")

    def open(self, tenant_id: str, item_id: str, stored: str) -> str:
        if self.cipher is None:
            return stored
        return str(self.cipher.open_value(stored, f"memory|{tenant_id}|{item_id}"))


def payload(item: MemoryItem) -> dict[str, Any]:
    """Everything about an item except its content and embedding, as JSON."""
    return item.model_dump(mode="json", exclude={"content", "score"})


def restore(meta: dict[str, Any], content: str, score: float | None = None) -> MemoryItem:
    return MemoryItem.model_validate({**meta, "content": content, "score": score})
