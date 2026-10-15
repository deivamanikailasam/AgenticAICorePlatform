"""``memory_store.pgvector`` against the stand-in Postgres (or Project 0)."""

import os

import psycopg
import pytest
from agent_contracts.dummies.embedder import HashingEmbedder
from agent_contracts.models import MemoryItem, MemoryScope, Provenance, Trust
from agent_contracts.testing import MemoryStoreContract
from agent_memory.pgvector import PgVectorMemoryStore
from agent_runtime.checkpoints import FieldCipher
from cryptography.exceptions import InvalidTag

pytestmark = pytest.mark.integration
KEY = os.urandom(32)


def dsn() -> str:
    value = os.environ.get("AGENT_DB_DSN")
    if not value:
        pytest.skip("AGENT_DB_DSN not set")
    return value


@pytest.fixture(autouse=True)
def _clean() -> None:
    with psycopg.connect(dsn(), autocommit=True) as conn:
        conn.execute("delete from agent_memory.items where tenant_id in ('t1', 't2')")


class TestPgVectorMemoryStoreContract(MemoryStoreContract):
    def make(self) -> PgVectorMemoryStore:
        return PgVectorMemoryStore(dsn(), HashingEmbedder(), FieldCipher(KEY))


async def test_content_is_encrypted_at_rest_and_bound_to_its_tenant() -> None:
    store = PgVectorMemoryStore(dsn(), HashingEmbedder(), FieldCipher(KEY))
    item = MemoryItem(
        scope=MemoryScope(tenant_id="t1", user_id="u1"),
        content="prefers invoices by post",
        provenance=Provenance(source="user", trust=Trust.USER),
    )
    await store.put(item)
    with psycopg.connect(dsn(), autocommit=True) as conn:
        (stored,) = conn.execute(
            "select content from agent_memory.items where id = %s", (item.id,)
        ).fetchone() or ("",)
        assert "invoices" not in stored
        conn.execute("update agent_memory.items set tenant_id = 't2' where id = %s", (item.id,))
    with pytest.raises(InvalidTag):  # moved to another tenant, it no longer decrypts
        await store.get(MemoryScope(tenant_id="t2", user_id="u1"), item.id)
