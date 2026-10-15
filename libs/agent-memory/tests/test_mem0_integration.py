"""``memory_store.mem0`` (Mem0 OSS on pgvector) against the stand-in Postgres (or Project 0)."""

import os
import uuid

import psycopg
import pytest
from agent_contracts.dummies.embedder import HashingEmbedder
from agent_contracts.models import MemoryItem, MemoryScope, Provenance, Trust
from agent_contracts.testing import MemoryStoreContract
from agent_memory.mem0 import Mem0MemoryStore

pytestmark = pytest.mark.integration


def dsn() -> str:
    value = os.environ.get("AGENT_DB_DSN")
    if not value:
        pytest.skip("AGENT_DB_DSN not set")
    return value


class TestMem0MemoryStoreContract(MemoryStoreContract):
    def make(self) -> Mem0MemoryStore:
        name = f"test_mem0_{uuid.uuid4().hex[:8]}"
        self.tables = [*getattr(self, "tables", []), name]
        return Mem0MemoryStore(dsn(), HashingEmbedder(), dims=256, collection=name)

    @pytest.fixture(autouse=True)
    def _drop(self) -> object:
        yield
        with psycopg.connect(dsn(), autocommit=True) as conn:
            for name in getattr(self, "tables", []):
                conn.execute(f'drop table if exists "{name}"')  # test-made names only


async def test_stored_content_is_exactly_what_the_policy_approved() -> None:
    """No model rewrites the item after the policy: Mem0's fact extraction is off."""
    name = f"test_mem0_{uuid.uuid4().hex[:8]}"
    store = Mem0MemoryStore(dsn(), HashingEmbedder(), dims=256, collection=name)
    text = "Vendor 42 invoices arrive on the first business day of the month."
    item = MemoryItem(
        scope=MemoryScope(tenant_id="t1", user_id="u1"),
        content=text,
        provenance=Provenance(source="user", trust=Trust.USER),
    )
    await store.put(item)
    got = await store.get(MemoryScope(tenant_id="t1", user_id="u1"), item.id)
    assert got is not None
    assert got.content == text
    with psycopg.connect(dsn(), autocommit=True) as conn:
        conn.execute(f'drop table if exists "{name}"')
