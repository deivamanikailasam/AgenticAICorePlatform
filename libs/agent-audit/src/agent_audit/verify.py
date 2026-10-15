"""Verifying a hash chain (Step 31): recompute every hash and name the first broken record."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from agent_contracts.dummies.observability import GENESIS, chain_hash
from agent_contracts.models import AuditRecord


@dataclass(frozen=True)
class Verdict:
    ok: bool
    records: int
    first_broken: int | None = None
    reason: str | None = None


def check_records(records: Iterable[AuditRecord]) -> Verdict:
    """The verdict on a chain of records (a JSONL trail, or records read back from anywhere)."""
    prev, count = GENESIS, 0
    for r in records:
        if r.seq != count:
            return Verdict(False, count, count, f"record {count} is missing (next is {r.seq})")
        if r.prev_hash != prev:
            return Verdict(False, count, r.seq, "prev_hash does not link to the record before")
        if r.hash != chain_hash(prev, r.seq, r.event):
            return Verdict(False, count, r.seq, "hash does not match the event")
        prev, count = r.hash, count + 1
    return Verdict(True, count)
