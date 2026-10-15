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
