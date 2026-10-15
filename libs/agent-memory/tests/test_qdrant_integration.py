"""``memory_store.qdrant`` against the stand-in Qdrant (or Project 0's)."""

import os
import uuid

import pytest
from agent_contracts.dummies.embedder import HashingEmbedder
from agent_contracts.testing import MemoryStoreContract
from agent_memory.qdrant import QdrantMemoryStore
from agent_runtime.checkpoints import FieldCipher

pytestmark = pytest.mark.integration


def url() -> str:
    value = os.environ.get("P0_QDRANT_URL")
    if not value:
        pytest.skip("P0_QDRANT_URL not set")
    return value


class TestQdrantMemoryStoreContract(MemoryStoreContract):
    def make(self) -> QdrantMemoryStore:
        return QdrantMemoryStore(
            url(),
            HashingEmbedder(),
            FieldCipher(os.urandom(32)),
            api_key=os.environ.get("SECRET__PROJECT0__QDRANT__API_KEY"),
            prefix=f"test_memory_{uuid.uuid4().hex[:8]}",  # a fresh collection per test
        )
