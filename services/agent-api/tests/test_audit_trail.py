"""Step 31: the audit trail of real runs through the run API.

Every event is a catalogued type with its required fields; every event of a run names the user
and agent, carries the versions in force (spec, plan, prompts, models, tools) and the run's trace
id; tool calls carry the arguments' digest and a redacted copy; the run is bracketed by
``run.started``/``plan`` and ``run.ended``; an approval's decision is bound to the same digest the
tool call records.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from agent_audit.events import validate
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hitl = _load("test_hitl")
gate = _load("test_registry_gate")


def run_scoped(records: list[Any], run_id: str) -> list[Any]:
    return [r.event for r in records if r.event.ctx.run_id.split("/")[0] == run_id]


async def test_an_approved_payment_leaves_a_complete_versioned_trail(
    span_exporter: InMemorySpanExporter,
) -> None:
    w = hitl.World()
    async with w.client() as c:
        run = await w.start(c)
        resp = await hitl.decide(
            c, run, "t-jsmith", decision="approve", arguments={"amount_aud": 40.0}
        )
        assert resp.status_code == 202, resp.text
        await w.manager.wait_idle(run)
    assert run.status == "succeeded"

    events = run_scoped(list(await w.audit.read()), run.run_id)
    problems = [p for e in events for p in validate(e)]
    assert not problems, problems
    types = [e.type for e in events]
    assert types[:2] == ["run.started", "plan"]
    assert types[-1] == "run.ended"
    assert types.count("run.started") == 2  # the start and the resume after the approval
    assert {"approval.requested", "approval.decided", "tool.call"} <= set(types)

    trace_ids = {s.context.trace_id for s in span_exporter.get_finished_spans()}
    for e in events:
        assert e.ctx.user_id
        assert e.ctx.agent_id == "pay-total"
        assert e.versions["spec"] == "0.1.0", e.type
        assert e.versions["plan_hash"]
        assert e.versions["tools"]["pay"] == "1.0.0"
        assert e.versions["models"]["executor"]
        assert e.trace_id is not None, e.type
        assert int(e.trace_id, 16) in trace_ids  # the trace exists

    pay = next(e for e in events if e.type == "tool.call" and e.payload["tool"] == "pay")
    decided = next(e for e in events if e.type == "approval.decided")
    assert pay.payload["arguments"] == {"amount_aud": 40.0}
    # the approval names exactly the call that ran: same tool, same (edited) arguments
    assert decided.payload["calls"] == [
        {"tool": "pay", "arguments_digest": pay.payload["arguments_digest"]}
    ]
    ended = next(e for e in events if e.type == "run.ended" and e.payload["status"] == "succeeded")
    assert len(ended.payload["output_digest"]) == 64


async def test_a_team_run_records_delegations_and_worker_calls_under_the_parent() -> None:
    manager, app, audit, _ = await gate.world("dev", [])
    resp = await gate.start(app, "research-report")
    run = manager.runs[resp.json()["run_id"]]
    await manager.wait_idle(run)
    assert run.status == "interrupted"  # the reviewed report waits for approval to publish
    events = run_scoped(list(await audit.read()), run.run_id)
    assert not [p for e in events for p in validate(e)]
    delegations = [e for e in events if e.type == "agent.delegate"]
    assert {e.payload["worker"] for e in delegations} == {
        "researcher",
        "analyst",
        "writer",
        "critic",
    }
    team_run = f"{run.run_id}/research"  # the team, nested in the app as a subgraph (Step 40)
    worker_calls = [e for e in events if e.type == "tool.call" and e.ctx.parent_run_id == team_run]
    assert worker_calls, "worker tool calls are recorded as child runs of the team run"
    for e in worker_calls:
        assert e.ctx.agent_id == "research-report-researcher"
        assert e.versions["spec"]  # the worker's own versions
        assert e.versions["tools"] == {"search": e.versions["tools"]["search"]}


async def _paid_run(checkpointer: Any = None, audit: Any = None) -> tuple[Any, Any, Any]:
    from agent_contracts.dummies.state import InMemoryCheckpointer

    checkpointer = checkpointer or InMemoryCheckpointer()
    w = hitl.World(checkpointer=checkpointer, audit=audit)
    async with w.client() as c:
        run = await w.start(c)
        resp = await hitl.decide(
            c, run, "t-jsmith", decision="approve", arguments={"amount_aud": 40.0}
        )
        assert resp.status_code == 202, resp.text
        await w.manager.wait_idle(run)
    assert run.status == "succeeded"
    records = list(await w.audit.read(run.run_id))
    return run, records, list(await checkpointer.list(run.ctx.tenant_id, run.thread_id))


async def test_a_run_is_reconstructed_from_audit_plus_checkpoints() -> None:
    from agent_audit.reconstruct import reconstruct

    run, records, points = await _paid_run()
    assert points, "the run was checkpointed"
    story = reconstruct(run.run_id, records, points)
    assert story.ok, story.render()
    assert (story.agent_id, story.user_id, story.status) == (
        "pay-total",
        run.ctx.user_id,
        "succeeded",
    )
    assert story.versions["spec"] == "0.1.0"
    assert story.trace_ids
    assert story.output is not None  # rebuilt from the checkpoints; its digest matched run.ended
    kinds = [(s.source, s.kind) for s in story.steps]
    assert kinds[0] == ("audit", "run.started")
    assert ("audit", "approval.decided") in kinds
    assert ("checkpoint", "approval") in kinds
    assert ("checkpoint", "node") in kinds
    text = story.render()
    assert "pay ok=True args={'amount_aud': 40.0}" in text
    assert "approve by" in text


async def test_reconstruction_catches_records_that_disagree() -> None:
    from agent_audit.reconstruct import reconstruct
    from agent_contracts.models import AuditRecord

    run, records, points = await _paid_run()
    # a checkpoint whose value was changed after the fact
    last = max((p for p in points if p.node_id in run.plan.exits), key=lambda p: p.step)
    forged = last.model_copy(update={"state": {**last.state, "value": "something else"}})
    story = reconstruct(run.run_id, records, [forged if p is last else p for p in points])
    assert {c.name for c in story.checks if not c.ok} == {"output matches run.ended"}

    # a payment that ran with arguments other than the approved ones
    def other_amount(r: AuditRecord) -> AuditRecord:
        if r.event.type != "tool.call" or r.event.payload.get("tool") != "pay":
            return r
        event = r.event.model_copy(
            update={"payload": {**r.event.payload, "arguments_digest": "0" * 64}}
        )
        return r.model_copy(update={"event": event})

    story = reconstruct(run.run_id, [other_amount(r) for r in records], points)
    failed = {c.name for c in story.checks if not c.ok}
    assert failed == {"approved calls ran with the approved arguments", "events match their hashes"}


@pytest.mark.integration
async def test_just_audit_replay_reconstructs_a_run_from_postgres(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import base64
    import os

    from agent_audit.postgres import PostgresAuditSink
    from agent_runtime.checkpoints import FieldCipher, PostgresCheckpointer

    from scripts.audit_replay import run as replay_cli

    dsn = os.environ.get("AGENT_DB_DSN", "postgresql://agent_app:standin-only@127.0.0.1:5432/agent")
    key = os.urandom(32)
    run, _, _ = await _paid_run(PostgresCheckpointer(dsn, FieldCipher(key)), PostgresAuditSink(dsn))
    monkeypatch.setenv("AGENT_ENV", "test")
    monkeypatch.setenv("SECRET__AGENT_PLATFORM__CHECKPOINTS__KEY", base64.b64encode(key).decode())
    for var in ("P0_OPENBAO_URL", "AGENT_BAO_ROLE_ID", "AGENT_BAO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert await replay_cli(run.run_id, dsn, dsn) == 0
    out = capsys.readouterr().out
    assert f"run {run.run_id}" in out
    assert "checkpoint approval" in out.replace("  ", " ").replace("  ", " ")
    assert "FAIL" not in out
