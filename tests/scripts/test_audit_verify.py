"""Step 31: ``just audit-verify`` passes an intact trail and names the first broken record."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from agent_audit.postgres import PostgresAuditSink
from agent_contracts.dummies.observability import JsonlAuditSink
from agent_contracts.models import AuditEvent, RunContext

from scripts.audit_verify import main

CTX = RunContext(tenant_id="acme", user_id="alice", agent_id="a", agent_version="1.0.0")


async def _trail(path: Path, n: int = 4) -> None:
    sink = JsonlAuditSink(str(path))
    for i in range(n):
        await sink.append(AuditEvent(type="tool.call", ctx=CTX, payload={"amount": i}))


async def test_an_intact_jsonl_trail_verifies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await _trail(tmp_path / "a.jsonl")
    assert main(["--file", str(tmp_path / "a.jsonl")]) == 0
    assert "OK, 4 records" in capsys.readouterr().out


async def test_an_edited_record_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "a.jsonl"
    await _trail(path)
    lines = path.read_text().splitlines()
    doc = json.loads(lines[2])
    doc["event"]["payload"]["amount"] = 9000
    lines[2] = json.dumps(doc)
    path.write_text("\n".join(lines) + "\n")
    assert main(["--file", str(path)]) == 1
    assert "BROKEN at record 2: hash does not match the event" in capsys.readouterr().out


async def test_a_removed_record_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "a.jsonl"
    await _trail(path)
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
    assert main(["--file", str(path)]) == 1
    assert "BROKEN at record 1: record 1 is missing" in capsys.readouterr().out


def test_nothing_to_check_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_AUDIT_DSN", raising=False)
    monkeypatch.delenv("AGENT_DB_DSN", raising=False)
    assert main([]) == 2


@pytest.mark.integration
def test_the_shared_postgres_trail_verifies(capsys: pytest.CaptureFixture[str]) -> None:
    dsn = os.environ.get("AGENT_DB_DSN", "postgresql://agent_app:standin-only@127.0.0.1:5432/agent")
    event = AuditEvent(type="run.started", ctx=CTX, payload={"input_digest": "x"})
    asyncio.run(PostgresAuditSink(dsn).append(event))
    # other tests' writers share the trail: whatever is in it must be one intact chain
    assert main(["--dsn", dsn]) == 0, capsys.readouterr().out
    assert "agent_audit.events: OK" in capsys.readouterr().out
