"""Step 31: the audit catalogue, redacted arguments, versions and trace ids on every event, and the
hash-chained Postgres trail (verifier, append-only, tamper detection)."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import psycopg
import pytest
from agent_audit.events import CATALOGUE, validate
from agent_audit.postgres import PostgresAuditSink
from agent_audit.redact import REDACTED, audited_arguments
from agent_audit.stamping import StampingAuditSink, agent_versions
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.models import AuditEvent, RunContext, arguments_digest
from agent_contracts.testing import AuditSinkContract
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

CTX = RunContext(
    tenant_id="acme", user_id="alice", agent_id="invoice-triage", agent_version="1.2.0"
)
DSN = os.environ.get("AGENT_DB_DSN", "postgresql://agent_app:standin-only@127.0.0.1:5432/agent")
ADMIN_DSN = os.environ.get(
    "P0_POSTGRES_ADMIN_DSN", "postgresql://postgres:standin-only@127.0.0.1:5432/postgres"
)


# ---------------------------------------------------------------- unit


def test_the_catalogue_covers_the_spec_event_types() -> None:
    # Step 31: run started/ended, plan, tool call, policy decision, guardrail result, approval
    # request and decision, memory write, delegation, A2A call
    for name in (
        "run.started",
        "run.ended",
        "plan",
        "tool.call",
        "policy.decision",
        "guardrail.result",
        "approval.requested",
        "approval.decided",
        "memory.write",
        "agent.delegate",
        "a2a.call",
    ):
        assert name in CATALOGUE
    assert validate(AuditEvent(type="tool.call", ctx=CTX, payload={"tool": "t"})) == [
        "tool.call: missing call_id, ok, arguments_digest"
    ]
    assert "unknown audit event type" in validate(AuditEvent(type="made.up", ctx=CTX))[0]


def test_arguments_are_hashed_and_recorded_redacted() -> None:
    args = {
        "amount_aud": 42.0,
        "payee": "Acme Pty Ltd",
        "api_key": "abc",
        "note": "use AKIAABCDEFGHIJKLMNOP please",  # secret-scan: allow
        "card": {"number": "4111111111111111"},
        "lines": [{"password": "x"}],
        "memo": "y" * 500,
    }
    audited = audited_arguments(args)
    assert audited["arguments_digest"] == arguments_digest(args)  # what approvals bind to
    shown = audited["arguments"]
    assert shown["amount_aud"] == 42.0
    assert shown["payee"] == "Acme Pty Ltd"
    assert shown["api_key"] == REDACTED
    assert shown["card"] == REDACTED
    assert shown["lines"] == [{"password": REDACTED}]
    assert "AKIAABCDEFGHIJKLMNOP" not in shown["note"]  # secret-scan: allow
    assert shown["note"] == "use <AWS_ACCESS_KEY> please"
    assert len(shown["memo"]) < 250


async def test_every_event_carries_versions_and_the_trace_id(
    span_exporter: InMemorySpanExporter,
) -> None:
    from agent_compiler import compile_spec
    from agent_spec import load_spec
    from opentelemetry import trace

    spec = load_spec("agents/invoice-triage/agent.yaml")
    plan = compile_spec(spec)
    inner = MemoryAuditSink()
    sink = StampingAuditSink(inner)
    versions = agent_versions(spec, plan, {"executor": "local-qwen-fast"}, {"pay": "1.0.0"})
    sink.register("invoice-triage", versions)
    with trace.get_tracer("t").start_as_current_span("invoke_agent invoice-triage") as span:
        record = await sink.append(
            AuditEvent(type="run.started", ctx=CTX, payload={"input_digest": "x"})
        )
    event = record.event
    assert event.trace_id == format(span.get_span_context().trace_id, "032x")
    assert event.versions["spec"] == spec.version
    assert event.versions["plan_hash"] == plan.metadata["plan_hash"]
    assert event.versions["models"] == {"executor": "local-qwen-fast"}
    assert event.versions["tools"] == {"pay": "1.0.0"}
    assert len(event.versions["spec_hash"]) == 64
    assert await inner.verify()
    other = await sink.append(
        AuditEvent(type="run.started", ctx=CTX.model_copy(update={"agent_id": "x"}))
    )
    assert other.event.versions == {}  # unregistered agents get none, never another's


def test_chains_written_before_step_31_still_verify() -> None:
    from agent_contracts.dummies.observability import chain_hash

    old = AuditEvent(type="tool.call", ctx=CTX, payload={"tool": "t"})
    body = old.model_dump(mode="json")
    body.pop("trace_id")
    body.pop("versions")
    assert old.chained() == body  # the hashed body is unchanged when the new fields are empty
    assert chain_hash("0" * 64, 0, old)


# ---------------------------------------------------------------- Postgres (stand-in)


async def _reset() -> None:
    """Empty the trail for the tests (as the database owner bypassing the append-only trigger,
    which only an administrator can do)."""
    async with await psycopg.AsyncConnection.connect(
        psycopg.conninfo.make_conninfo(ADMIN_DSN, dbname="agent"), autocommit=True
    ) as conn:
        await conn.execute("set session_replication_role = replica")
        await conn.execute("delete from agent_audit.events")


@pytest.mark.integration
class TestPostgresAuditSink(AuditSinkContract):
    def make(self) -> PostgresAuditSink:
        return PostgresAuditSink(DSN)

    async def test_chain_links_and_verifies(self) -> None:
        await _reset()
        await super().test_chain_links_and_verifies()


@pytest.mark.integration
async def test_concurrent_writers_keep_one_unbroken_chain() -> None:
    await _reset()
    writers = [PostgresAuditSink(DSN) for _ in range(4)]

    async def write(sink: PostgresAuditSink, i: int) -> None:
        for j in range(10):
            await sink.append(AuditEvent(type="tool.call", ctx=CTX, payload={"i": i, "j": j}))

    await asyncio.gather(*(write(s, i) for i, s in enumerate(writers)))
    verdict = await writers[0].check()
    assert verdict.ok, verdict
    assert verdict.records == 40


@pytest.mark.integration
async def test_the_trail_is_append_only() -> None:
    await _reset()
    sink = PostgresAuditSink(DSN)
    await sink.append(AuditEvent(type="run.started", ctx=CTX, payload={"input_digest": "x"}))
    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as conn:
        for sql in (
            "update agent_audit.events set type = 'x'",
            "delete from agent_audit.events",
            "truncate agent_audit.events",
        ):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                await conn.execute(sql)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("tamper", "reason"),
    [
        (
            "update agent_audit.events set event = jsonb_set(event, '{payload,amount}', '9000') where seq = 2",
            "hash does not match",
        ),
        ("delete from agent_audit.events where seq = 2", "missing"),
        (
            "update agent_audit.events set prev_hash = repeat('0', 64) where seq = 3",
            "does not link",
        ),
    ],
)
async def test_tampering_with_one_event_is_detected(tamper: str, reason: str) -> None:
    await _reset()
    sink = PostgresAuditSink(DSN)
    for i in range(5):
        await sink.append(AuditEvent(type="tool.call", ctx=CTX, payload={"amount": 40 + i}))
    assert await sink.verify()
    async with await psycopg.AsyncConnection.connect(
        psycopg.conninfo.make_conninfo(ADMIN_DSN, dbname="agent"), autocommit=True
    ) as conn:
        await conn.execute("set session_replication_role = replica")  # an admin bypassing triggers
        await conn.execute(tamper)
    verdict = await sink.check()
    await _reset()  # leave the shared trail intact for the other writers
    assert not verdict.ok
    assert verdict.first_broken in (2, 3)
    assert reason in (verdict.reason or "")


def test_services_write_the_shared_trail_when_configured(tmp_path: Any) -> None:
    from agent_audit.factory import audit_sink_from_env
    from agent_contracts.dummies.observability import JsonlAuditSink

    local = str(tmp_path / "a.jsonl")
    assert isinstance(audit_sink_from_env(local, {"AGENT_AUDIT_DSN": DSN}), PostgresAuditSink)
    assert isinstance(audit_sink_from_env(local, {"AGENT_ENV": "ci"}), JsonlAuditSink)
    # the agent database alone does not move the trail
    assert isinstance(audit_sink_from_env(local, {"AGENT_DB_DSN": DSN}), JsonlAuditSink)
    for stage in ("staging", "prod"):
        with pytest.raises(RuntimeError, match="shared Postgres trail"):
            audit_sink_from_env(local, {"AGENT_ENV": stage})
