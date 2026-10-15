"""Postgres and Valkey checkpointers against the stand-in stack (or Project 0)."""

import os
import uuid
from datetime import timedelta

import psycopg
import pytest
from agent_contracts.models import Checkpoint
from agent_contracts.testing import CheckpointerContract
from agent_runtime.checkpoints import FieldCipher, PostgresCheckpointer, ValkeyCheckpointer

pytestmark = pytest.mark.integration
KEY = os.urandom(32)


def dsn() -> str:
    value = os.environ.get("AGENT_DB_DSN")
    if not value:
        pytest.skip("AGENT_DB_DSN not set")
    return value


class TestPostgresCheckpointerContract(CheckpointerContract):
    def make(self) -> PostgresCheckpointer:
        return PostgresCheckpointer(dsn(), FieldCipher(KEY))

    @pytest.fixture(autouse=True)
    def _clean(self) -> None:
        with psycopg.connect(dsn(), autocommit=True) as conn:
            conn.execute("delete from agent_runtime.checkpoints where thread_id = 'th'")


class TestValkeyCheckpointerContract(CheckpointerContract):
    def make(self) -> ValkeyCheckpointer:
        return ValkeyCheckpointer(
            os.environ.get("P0_VALKEY_URL", "redis://127.0.0.1:6379/0"),
            FieldCipher(KEY),
            prefix=f"test-cp-{uuid.uuid4().hex[:8]}:",
        )


async def test_sensitive_fields_are_ciphertext_at_rest() -> None:
    cp = PostgresCheckpointer(dsn(), FieldCipher(KEY))
    thread = uuid.uuid4().hex
    await cp.put(
        Checkpoint(tenant_id="t1", thread_id=thread, state={"value": {"iban": "DE89-SECRET"}})
    )
    with psycopg.connect(dsn()) as conn:
        raw = conn.execute(
            "select state::text from agent_runtime.checkpoints where thread_id = %s", (thread,)
        ).fetchone()
    assert raw is not None
    assert "DE89-SECRET" not in raw[0]
    latest = await cp.get("t1", thread)
    assert latest is not None
    assert latest.state["value"] == {"iban": "DE89-SECRET"}


async def test_retention_keeps_the_newest_per_thread() -> None:
    cp = PostgresCheckpointer(dsn(), FieldCipher(KEY))
    thread = uuid.uuid4().hex
    old = [Checkpoint(tenant_id="t1", thread_id=thread, step=i) for i in range(3)]
    for c in old:
        await cp.put(c.model_copy(update={"created_at": c.created_at - timedelta(days=60)}))
    removed = await cp.prune(older_than=timedelta(days=30), keep_last=1)
    assert removed >= 2
    assert [c.step for c in await cp.list("t1", thread)] == [2]
