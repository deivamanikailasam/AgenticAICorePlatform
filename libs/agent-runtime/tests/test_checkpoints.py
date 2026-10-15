"""Checkpoint encryption and time travel (Step 18), on the SQLite test backend."""

import os

import pytest
from agent_contracts.dummies.state import SqliteCheckpointer
from agent_contracts.models import Checkpoint
from agent_runtime.checkpoints import FieldCipher, fork_thread, history
from cryptography.exceptions import InvalidTag

KEY = os.urandom(32)


def test_sensitive_fields_are_sealed_and_bound_to_their_row() -> None:
    cipher = FieldCipher(KEY)
    cp = Checkpoint(
        tenant_id="t1",
        thread_id="th",
        node_id="pay",
        state={"outcome": "ok", "value": {"card": "4111"}},
    )
    sealed = cipher.seal(cp)
    assert sealed["outcome"] == "ok", "non-sensitive fields stay readable for indexing"
    assert "4111" not in str(sealed["value"])
    assert cipher.open(cp.model_copy(update={"state": sealed})).state == cp.state
    moved = cp.model_copy(update={"tenant_id": "t2", "state": sealed})
    with pytest.raises(InvalidTag):
        cipher.open(moved)
    with pytest.raises(ValueError, match="32-byte key"):
        FieldCipher(b"short")


async def test_history_and_fork_from_an_earlier_checkpoint() -> None:
    cp = SqliteCheckpointer()
    parent = None
    ids = []
    for step, node in enumerate(["validate", "resolve", "pay"]):
        c = Checkpoint(
            tenant_id="t1",
            thread_id="th",
            run_id="r1",
            step=step,
            node_id=node,
            parent_id=parent,
            state={"value": node},
        )
        await cp.put(c)
        parent = c.checkpoint_id
        ids.append(c.checkpoint_id)
    lines = await history(cp, "t1", "th")
    assert [line["node_id"] for line in lines] == ["validate", "resolve", "pay"]
    assert "state" not in lines[0]
    thread, run = await fork_thread(cp, "t1", "th", ids[1])
    forked = await cp.list("t1", thread)
    assert [c.node_id for c in forked] == ["validate", "resolve"]
    assert {c.run_id for c in forked} == {run}
    assert forked[1].parent_id == forked[0].checkpoint_id
    assert forked[1].state["forked_from"] == ids[1]
    assert len(await cp.list("t1", "th")) == 3, "the original thread is untouched"
    with pytest.raises(KeyError):
        await fork_thread(cp, "t1", "th", "nope")
    assert await cp.list("t2", thread) == [], "forks stay in their tenant"
