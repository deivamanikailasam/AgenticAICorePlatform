"""Reconstructing a run from its audit trail plus its checkpoints (Step 31).

The audit trail says who ran which agent at which versions, what the plan was, which tools were
called with which (redacted) arguments, what policy and people decided and how the run ended. The
checkpoints hold what each node produced. Put together, in time order, they tell the run's whole
story without the run store, the logs or the model provider.

``reconstruct`` also cross-checks the two sources, because a story built from records is only as
good as the records:

* every event of the run still matches its own hash (``just audit-verify`` checks the whole chain);
* every checkpointed node is a node of the audited plan;
* every approved call ran with exactly the approved arguments (same tool, same digest);
* the run's output, rebuilt from the checkpoints of the plan's exits, has the digest that
  ``run.ended`` recorded.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent_contracts.dummies.observability import chain_hash
from agent_contracts.interfaces import AuditSink, Checkpointer
from agent_contracts.models import AuditRecord, Checkpoint

from agent_audit.redact import digest


@dataclass(frozen=True)
class Step:
    at: datetime
    source: str  # "audit" or "checkpoint"
    kind: str  # audit event type, or the checkpoint's kind (node, approval)
    run_id: str
    summary: str


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Reconstruction:
    run_id: str
    tenant_id: str = ""
    user_id: str = ""
    agent_id: str = ""
    versions: dict[str, Any] = field(default_factory=dict)
    trace_ids: list[str] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)
    status: str | None = None
    output: Any = None
    steps: list[Step] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def render(self) -> str:
        lines = [
            f"run {self.run_id}  tenant {self.tenant_id}  user {self.user_id}",
            f"agent {self.agent_id} {self.versions.get('spec', '?')}"
            f"  plan {str(self.versions.get('plan_hash', '?'))[:12]}"
            f"  models {self.versions.get('models', {})}  tools {self.versions.get('tools', {})}",
            f"trace {', '.join(self.trace_ids) or '-'}",
            f"status {self.status or 'unknown'}",
            "",
        ]
        for s in self.steps:
            child = "" if s.run_id == self.run_id else f" [{s.run_id.split('/', 1)[-1]}]"
            lines.append(
                f"{s.at.isoformat(timespec='milliseconds')}  {s.source:<10} {s.kind:<18}"
                f"{child} {s.summary}"
            )
        lines.append("")
        for c in self.checks:
            lines.append(
                f"{'PASS' if c.ok else 'FAIL'}  {c.name}{': ' + c.detail if c.detail else ''}"
            )
        return "\n".join(lines)


def _summary(kind: str, p: dict[str, Any]) -> str:
    if kind == "tool.call":
        return f"{p.get('tool')} ok={p.get('ok')} args={p.get('arguments')}"
    if kind == "policy.decision":
        return f"{p.get('tool')} {p.get('decision')} {p.get('reasons') or ''}".rstrip()
    if kind.startswith("approval."):
        who = f" by {p['by']}" if p.get("by") else ""
        return f"{p.get('approval_id')} {p.get('decision') or ''}{who} tools={p.get('tools')}"
    if kind == "plan":
        return f"{len(p.get('nodes', []))} nodes, entry {p.get('entry')}"
    if kind == "run.started":
        return "resumed" if p.get("resumed") else "started"
    if kind == "run.ended":
        return f"{p.get('status')} stop={p.get('stop_reason')} cost_aud={p.get('cost_aud')}"
    if kind == "agent.delegate":
        return f"{p.get('worker')} {p.get('effect')}"
    keys = ("tool", "decision", "action", "stage", "worker", "agent")
    return " ".join(f"{k}={p[k]}" for k in keys if k in p)


def _in_run(run_id: str, candidate: str | None) -> bool:
    return candidate is not None and (candidate == run_id or candidate.startswith(f"{run_id}/"))


def reconstruct(
    run_id: str, records: Sequence[AuditRecord], checkpoints: Sequence[Checkpoint]
) -> Reconstruction:
    """The story of ``run_id`` (and its team workers' child runs) from the two sources."""
    mine = [r for r in records if _in_run(run_id, r.event.ctx.run_id)]
    points = [c for c in checkpoints if _in_run(run_id, c.run_id)]
    rec = Reconstruction(run_id)
    own = [r.event for r in mine if r.event.ctx.run_id == run_id]
    if own:
        first = own[0]
        rec.tenant_id, rec.user_id, rec.agent_id = (
            first.ctx.tenant_id,
            first.ctx.user_id or "",
            first.ctx.agent_id,
        )
        rec.versions = next((e.versions for e in own if e.versions), {})
    for e in own:
        if e.trace_id and e.trace_id not in rec.trace_ids:
            rec.trace_ids.append(e.trace_id)
    rec.plan = next((e.payload for e in own if e.type == "plan"), {})
    ended = [e for e in own if e.type == "run.ended"]
    rec.status = str(ended[-1].payload.get("status")) if ended else None

    for r in mine:
        e = r.event
        rec.steps.append(Step(e.at, "audit", e.type, e.ctx.run_id, _summary(e.type, e.payload)))
    for c in points:
        state = c.state
        text = f"{c.node_id} {state.get('outcome')}"
        if state.get("error"):
            text += f" error={state['error']}"
        rec.steps.append(
            Step(c.created_at, "checkpoint", str(state.get("kind", "node")), str(c.run_id), text)
        )
    rec.steps.sort(key=lambda s: s.at)

    rec.checks.append(_hashes(mine))
    rec.checks.append(_bracketed(own))
    rec.checks.append(_plan_covers(rec.plan, [c for c in points if c.run_id == run_id]))
    rec.checks.append(_approvals_bound([r.event for r in mine]))
    output_check, rec.output = _output(rec.plan, ended, [c for c in points if c.run_id == run_id])
    rec.checks.append(output_check)
    return rec


def _hashes(records: Sequence[AuditRecord]) -> Check:
    bad = [r.seq for r in records if r.hash != chain_hash(r.prev_hash, r.seq, r.event)]
    if bad:
        return Check("events match their hashes", False, f"records {bad} were changed")
    return Check("events match their hashes", True, f"{len(records)} events")


def _bracketed(own: Sequence[Any]) -> Check:
    types = [e.type for e in own]
    if not types:
        return Check("run started and ended", False, "no audit events for this run")
    if types[0] != "run.started":
        return Check("run started and ended", False, f"first event is {types[0]}")
    if "run.ended" not in types:
        return Check("run started and ended", False, "no run.ended (still running, or lost)")
    return Check("run started and ended", True)


def _plan_covers(plan: dict[str, Any], points: Sequence[Checkpoint]) -> Check:
    if not plan:
        return Check("checkpoints follow the audited plan", not points, "no plan event")
    nodes = {n["id"] for n in plan.get("nodes", [])}
    stray = sorted({str(c.node_id) for c in points if c.node_id and c.node_id not in nodes})
    if stray:
        return Check("checkpoints follow the audited plan", False, f"unknown nodes {stray}")
    return Check("checkpoints follow the audited plan", True, f"{len(points)} checkpoints")


def _approvals_bound(events: Sequence[Any]) -> Check:
    ran = {
        (e.payload.get("tool"), e.payload.get("arguments_digest"))
        for e in events
        if e.type == "tool.call" and e.payload.get("ok")
    }
    approved = [
        (c.get("tool"), c.get("arguments_digest"))
        for e in events
        if e.type == "approval.decided" and e.payload.get("decision") == "approve"
        for c in e.payload.get("calls", [])
    ]
    missing = [t for t, d in approved if (t, d) not in ran]
    if missing:
        return Check(
            "approved calls ran with the approved arguments", False, f"no matching call: {missing}"
        )
    return Check("approved calls ran with the approved arguments", True, f"{len(approved)} calls")


def _output(
    plan: dict[str, Any], ended: Sequence[Any], points: Sequence[Checkpoint]
) -> tuple[Check, Any]:
    name = "output matches run.ended"
    final = next((e for e in reversed(ended) if "output_digest" in e.payload), None)
    if final is None:
        return Check(name, False, "run.ended recorded no output digest"), None
    values: dict[str, Any] = {}
    for c in sorted(points, key=lambda c: c.step):
        if c.node_id and c.state.get("kind", "node") == "node":
            values[c.node_id] = c.state.get("value")
    exits = plan.get("exits", [])
    output = next((values[x] for x in reversed(exits) if x in values), None)
    if digest(output) != final.payload["output_digest"]:
        return Check(name, False, "the checkpoints' output has a different digest"), output
    return Check(name, True), output


async def replay(
    run_id: str, audit: AuditSink, checkpointer: Checkpointer | None
) -> Reconstruction:
    """Read a run's records from the trail and its checkpoints from the checkpointer (the threads
    the trail names), and reconstruct it."""
    records = list(await audit.read(run_id))
    points: list[Checkpoint] = []
    if checkpointer is not None:
        threads = {(r.event.ctx.tenant_id, r.event.ctx.thread_id) for r in records}
        for tenant, thread in sorted(threads):
            points.extend(await checkpointer.list(tenant, thread))
    return reconstruct(run_id, records, points)
