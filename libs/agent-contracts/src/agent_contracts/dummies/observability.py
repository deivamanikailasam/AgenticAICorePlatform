from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from agent_contracts.dummies._util import canonical
from agent_contracts.interfaces import AuditSink, TelemetryEmitter
from agent_contracts.models import AuditEvent, AuditRecord

GENESIS = "0" * 64


class MemoryTelemetryEmitter(TelemetryEmitter):
    """Keeps finished spans and metrics in lists, for assertions in tests."""

    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []
        self.metrics: list[tuple[str, float, dict[str, Any]]] = []
        self._stack: list[str] = []

    def span(
        self, name: str, attributes: Mapping[str, Any] | None = None
    ) -> AbstractContextManager[Any]:
        @contextmanager
        def _cm() -> Iterator[dict[str, Any]]:
            record = {
                "name": name,
                "attributes": dict(attributes or {}),
                "parent": self._stack[-1] if self._stack else None,
                "error": None,
            }
            self._stack.append(name)
            try:
                yield record
            except BaseException as exc:
                record["error"] = type(exc).__name__
                raise
            finally:
                self._stack.pop()
                self.spans.append(record)

        return _cm()

    def metric(self, name: str, value: float, attributes: Mapping[str, Any] | None = None) -> None:
        self.metrics.append((name, value, dict(attributes or {})))


class NoopTelemetryEmitter(TelemetryEmitter):
    """Discards everything (telemetry switched off)."""

    def span(
        self, name: str, attributes: Mapping[str, Any] | None = None
    ) -> AbstractContextManager[Any]:
        @contextmanager
        def _cm() -> Iterator[None]:
            yield None

        return _cm()

    def metric(self, name: str, value: float, attributes: Mapping[str, Any] | None = None) -> None:
        return None


def chain_hash(prev_hash: str, seq: int, event: AuditEvent) -> str:
    body = canonical({"prev": prev_hash, "seq": seq, "event": event.model_dump(mode="json")})
    return hashlib.sha256(body.encode()).hexdigest()


def verify_chain(records: Sequence[AuditRecord]) -> bool:
    prev = GENESIS
    for i, r in enumerate(records):
        if r.seq != i or r.prev_hash != prev or r.hash != chain_hash(prev, r.seq, r.event):
            return False
        prev = r.hash
    return True


class MemoryAuditSink(AuditSink):
    """Hash-chained records in a list."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    async def append(self, event: AuditEvent) -> AuditRecord:
        prev = self._records[-1].hash if self._records else GENESIS
        seq = len(self._records)
        record = AuditRecord(
            seq=seq, event=event, prev_hash=prev, hash=chain_hash(prev, seq, event)
        )
        self._records.append(record)
        return record

    async def read(self, run_id: str | None = None) -> Sequence[AuditRecord]:
        return [r for r in self._records if run_id is None or r.event.ctx.run_id == run_id]

    async def verify(self) -> bool:
        return verify_chain(self._records)

    def _corrupt(self, seq: int) -> None:
        """Test hook: alter one stored event without fixing its hash."""
        r = self._records[seq]
        tampered = r.event.model_copy(update={"payload": {**r.event.payload, "tampered": True}})
        self._records[seq] = r.model_copy(update={"event": tampered})


class JsonlAuditSink(AuditSink):
    """Hash-chained records appended to a JSON Lines file (a temp file by default)."""

    def __init__(self, path: str | None = None) -> None:
        if path is None:
            _, path = tempfile.mkstemp(prefix="audit-", suffix=".jsonl")
            Path(path).write_text("")
        self.path = Path(path)
        self.path.touch()

    def _load(self) -> list[AuditRecord]:
        return [
            AuditRecord.model_validate_json(line)
            for line in self.path.read_text().splitlines()
            if line
        ]

    async def append(self, event: AuditEvent) -> AuditRecord:
        records = self._load()
        prev = records[-1].hash if records else GENESIS
        seq = len(records)
        record = AuditRecord(
            seq=seq, event=event, prev_hash=prev, hash=chain_hash(prev, seq, event)
        )
        with self.path.open("a") as fh:
            fh.write(record.model_dump_json() + "\n")
        return record

    async def read(self, run_id: str | None = None) -> Sequence[AuditRecord]:
        return [r for r in self._load() if run_id is None or r.event.ctx.run_id == run_id]

    async def verify(self) -> bool:
        return verify_chain(self._load())

    def _corrupt(self, seq: int) -> None:
        lines = self.path.read_text().splitlines()
        doc = json.loads(lines[seq])
        doc["event"]["payload"]["tampered"] = True
        lines[seq] = json.dumps(doc)
        self.path.write_text("\n".join(lines) + "\n")
