"""Build a run's memory service from the spec's ``memory.long_term`` section.

Stores and policies are created by name from the component registry. Store collaborators come
from the environment: ``AGENT_DB_DSN`` (pgvector, mem0), ``P0_QDRANT_URL`` and the Qdrant key
from the secret store (qdrant); the content encryption key is the checkpoint key unless
``AGENT_MEMORY_KEY`` names another secret.
"""

from __future__ import annotations

import os
from typing import Any

from agent_contracts.interfaces import AuditSink, Embedder, MemoryPolicy, MemoryStore
from agent_contracts.registry import registry
from agent_runtime.checkpoints import FieldCipher
from agent_spec.model import LongTermMemory
from agent_tools.secrets import default_resolver

from agent_memory.service import MemoryService


async def store_for(
    name: str, embedder: Embedder | None, cipher: FieldCipher | None
) -> MemoryStore:
    kwargs: dict[str, Any] = {}
    if name in ("pgvector", "mem0"):
        dsn = os.environ.get("AGENT_DB_DSN")
        if not dsn:
            raise LookupError(f"memory store '{name}' needs AGENT_DB_DSN")
        kwargs = {"dsn": dsn, "embedder": embedder}
        if name == "pgvector":
            kwargs["cipher"] = cipher
    elif name == "qdrant":
        kwargs = {
            "url": os.environ.get("P0_QDRANT_URL", "http://127.0.0.1:6333"),
            "embedder": embedder,
            "cipher": cipher,
            "api_key": await default_resolver().resolve(
                os.environ.get("P0_QDRANT_API_KEY", "${secret:project0/qdrant#api_key}")
            ),
        }
    created = registry.create("memory_store", name, **kwargs)
    if not isinstance(created, MemoryStore):
        raise LookupError(f"memory store '{name}' is not a MemoryStore")
    return created


async def memory_for(
    config: LongTermMemory,
    *,
    embedder: Embedder | None = None,
    cipher: FieldCipher | None = None,
    audit: AuditSink | None = None,
    store: MemoryStore | None = None,
) -> MemoryService:
    policy = registry.create("memory_policy", config.policy)
    if not isinstance(policy, MemoryPolicy):
        raise LookupError(f"memory policy '{config.policy}' is not a MemoryPolicy")
    return MemoryService(
        store or await store_for(config.store, embedder, cipher),
        policy,
        audit=audit,
        level=config.scope,
        ttl_days=config.ttl_days,
    )
