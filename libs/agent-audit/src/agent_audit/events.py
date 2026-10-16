"""The audit event catalogue (Step 31): every event type the platform writes, what its payload
must carry, and a validator. A new event type is added here first; ``validate`` rejects types
that are not in the catalogue, so the trail stays a known, documented vocabulary.

Every event, whatever its type, also records who and what (``ctx``: tenant, user, agent, agent
version, run, thread, parent run), the versions in force (``versions``: spec version and hash,
plan hash, prompt refs, model per role, tool versions) and the trace id that joins it to Tempo
and Langfuse.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_contracts.models import AuditEvent


@dataclass(frozen=True)
class EventType:
    name: str
    meaning: str
    required: tuple[str, ...] = ()
    #: run-scoped events carry versions and (when traced) a trace id
    run_scoped: bool = True


CATALOGUE: dict[str, EventType] = {
    e.name: e
    for e in (
        # the run
        EventType("run.started", "A run (or a resume) began", ("input_digest",)),
        EventType("plan", "The compiled plan the run executes", ("plan_hash", "nodes")),
        EventType("run.ended", "A run reached an end state", ("status",)),
        EventType("run.taken_over", "An operator stopped the agent and finished the work", ("by",)),
        # tools and policy
        EventType(
            "policy.decision",
            "Policy refused a tool call or asked for approval",
            ("tool", "decision"),
        ),
        EventType(
            "tool.call",
            "A tool call ran (or was refused after policy, e.g. rate limit, credential)",
            ("tool", "call_id", "ok", "arguments_digest"),
        ),
        EventType(
            "mcp.call",
            "The MCP transport record of a call (server, protocol version, on behalf of)",
            ("server", "tool", "ok", "arguments_digest"),
        ),
        EventType(
            "mcp.tool_refused",
            "An MCP tool was not offered (description screen)",
            ("server", "tool"),
        ),
        EventType(
            "guardrail.result",
            "A guardrail redacted or blocked content",
            ("stage", "action", "findings"),
        ),
        # people
        EventType("approval.requested", "A run paused for a person's decision", ("approval_id",)),
        EventType("approval.partial", "The first of two required approvals", ("approval_id", "by")),
        EventType(
            "approval.decided",
            "A person approved, edited or rejected",
            ("approval_id", "decision", "by"),
        ),
        EventType(
            "approval.escalated", "An approval moved to the escalation group", ("approval_id",)
        ),
        EventType(
            "approval.expired", "An approval's SLA ran out; the run was cancelled", ("approval_id",)
        ),
        EventType("review.sampled", "A finished run was sampled for review", ()),
        EventType("review.completed", "A reviewer recorded a verdict", ("verdict", "by")),
        # memory, delegation, agents
        EventType("memory.write", "A long-term memory write was decided", ("decision",)),
        EventType("memory.flushed", "A run's memories were removed", ()),
        EventType("subject.erased", "A person's data was erased", ()),
        EventType(
            "subject.exported", "A person's data was exported (access request)", ("subject",)
        ),
        EventType("retention.applied", "Retention pruned old runs, checkpoints and memories", ()),
        EventType(
            "agent.delegate",
            "A team delegated work to a worker (policy decision)",
            ("worker", "effect"),
        ),
        EventType("a2a.call", "A call to a remote A2A agent", ("agent", "ok")),
        EventType("a2a.inbound", "A remote client called a published agent", ()),
        EventType("registry.refused", "An agent version was not allowed to run", ()),
    )
}


def validate(event: AuditEvent) -> list[str]:
    """Problems with an event: unknown type, or a required payload field missing."""
    kind = CATALOGUE.get(event.type)
    if kind is None:
        return [f"unknown audit event type {event.type!r}; add it to agent_audit.events"]
    missing = [k for k in kind.required if k not in event.payload]
    return [f"{event.type}: missing {', '.join(missing)}"] if missing else []
