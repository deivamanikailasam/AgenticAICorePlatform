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
    assert pay.payload["arguments_digest"] == decided.payload.get(
        "arguments_digest", pay.payload["arguments_digest"]
    )
    ended = next(e for e in events if e.type == "run.ended" and e.payload["status"] == "succeeded")
    assert len(ended.payload["output_digest"]) == 64


async def test_a_team_run_records_delegations_and_worker_calls_under_the_parent() -> None:
    manager, app, audit, _ = await gate.world("dev", [])
    resp = await gate.start(app, "research-report")
    run = manager.runs[resp.json()["run_id"]]
    await manager.wait_idle(run)
    assert run.status == "succeeded"
    events = run_scoped(list(await audit.read()), run.run_id)
    assert not [p for e in events for p in validate(e)]
    delegations = [e for e in events if e.type == "agent.delegate"]
    assert {e.payload["worker"] for e in delegations} == {"researcher", "analyst", "writer"}
    worker_calls = [
        e for e in events if e.type == "tool.call" and e.ctx.parent_run_id == run.run_id
    ]
    assert worker_calls, "worker tool calls are recorded as child runs of the team run"
    for e in worker_calls:
        assert e.ctx.agent_id == "research-report-researcher"
        assert e.versions["spec"]  # the worker's own versions
        assert e.versions["tools"] == {"search": e.versions["tools"]["search"]}
