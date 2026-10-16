"""The Postgres run store: runs and AG-UI events survive, encrypted, in the agent database."""

import os
import uuid

import psycopg
import pytest
from agent_api.store import PostgresRunStore
from agent_runtime.checkpoints import FieldCipher

pytestmark = pytest.mark.integration


async def test_runs_and_events_round_trip_and_are_ciphertext_at_rest() -> None:
    dsn = os.environ.get("AGENT_DB_DSN")
    if not dsn:
        pytest.skip("AGENT_DB_DSN not set")
    store = PostgresRunStore(dsn, FieldCipher(os.urandom(32)))
    run_id = uuid.uuid4().hex
    record = {
        "run_id": run_id,
        "tenant": "acme",
        "user": "alice",
        "groups": ["/acme/finance"],
        "thread_id": uuid.uuid4().hex,
        "agent": "invoice-triage",
        "status": "running",
        "inputs": {"invoice": {"iban": "AU-SECRET-123"}},
        "idempotency_key": None,
        "body_hash": None,
    }
    await store.save(record)
    await store.append(run_id, 0, {"type": "RUN_STARTED", "note": "AU-SECRET-123"})
    await store.save({**record, "status": "interrupted"})
    saved = next(r for r in await store.records() if r["run_id"] == run_id)
    assert saved["status"] == "interrupted"
    assert saved["inputs"] == record["inputs"]
    assert await store.events(run_id) == [{"type": "RUN_STARTED", "note": "AU-SECRET-123"}]
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "select r.doc || e.event from agent_runtime.runs r join agent_runtime.run_events e"
            " using (run_id) where run_id = %s",
            (run_id,),
        ).fetchone()
        conn.execute("delete from agent_runtime.runs where run_id = %s", (run_id,))
    assert row is not None
    assert "AU-SECRET-123" not in row[0]


async def test_leases_let_one_replica_drive_a_run_and_another_take_over() -> None:
    """Step 38: claim, holder, renew, lapse and take-over in Postgres; idempotency across
    replicas."""
    import asyncio

    from agent_api.store import IdempotencyRaceError

    dsn = os.environ.get("AGENT_DB_DSN")
    if not dsn:
        pytest.skip("AGENT_DB_DSN not set")
    store = PostgresRunStore(dsn, FieldCipher(os.urandom(32)))
    run_id, key = uuid.uuid4().hex, uuid.uuid4().hex
    record = {
        "run_id": run_id,
        "tenant": "acme",
        "user": "alice",
        "thread_id": uuid.uuid4().hex,
        "agent": "invoice-triage",
        "status": "running",
        "inputs": {},
        "idempotency_key": key,
        "body_hash": "h",
    }
    try:
        assert await store.claim(run_id, "a:8091", 30) is None, "no such run yet"
        await store.save(record)
        assert run_id in await store.orphans(["running"]), "nobody holds it yet"
        assert await store.claim(run_id, "a:8091", 0.5) == "a:8091"
        assert await store.claim(run_id, "b:8091", 30) == "a:8091", "a's lease is live"
        assert await store.holder(run_id) == "a:8091"
        assert run_id not in await store.orphans(["running"])
        assert await store.renew("a:8091", [run_id], 0.5) == {run_id}
        assert await store.renew("b:8091", [run_id], 30) == set(), "b holds nothing"
        await asyncio.sleep(0.7)  # a stops renewing
        assert await store.holder(run_id) is None
        assert run_id in await store.orphans(["running"])
        assert await store.claim(run_id, "b:8091", 30) == "b:8091", "b takes over"
        assert await store.renew("a:8091", [run_id], 30) == set(), "a has lost it"
        assert (await store.record(run_id) or {})["agent"] == "invoice-triage"
        assert (await store.find_idempotent("acme", "alice", key) or {})["run_id"] == run_id
        with pytest.raises(IdempotencyRaceError):  # the same key from another replica
            await store.save({**record, "run_id": uuid.uuid4().hex})
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("delete from agent_runtime.runs where idempotency_key = %s", (key,))
