"""Specs into the registry, and the approved-only gate (Step 25)."""

from datetime import timedelta
from pathlib import Path

from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.registry import InMemoryAgentRegistry
from agent_contracts.models import AgentStatus, RunContext, utcnow
from agent_registry.gate import ApprovedOnly
from agent_registry.sync import load_all, record_of, sync
from agent_spec import load_spec, load_spec_text

ROOT = Path(__file__).resolve().parents[3]
HELLO = ROOT / "agents" / "hello-workflow" / "agent.yaml"


def ctx() -> RunContext:
    return RunContext(tenant_id="acme", user_id="alice", agent_id="hello-workflow")


async def test_every_spec_is_registered_once_as_draft() -> None:
    reg = InMemoryAgentRegistry()
    specs = load_all(ROOT / "agents")
    first = await sync(reg, specs)
    assert {r.outcome for r in first} == {"registered"}
    assert {r.name for r in first} >= {"hello-workflow", "research-report-writer"}  # workers too
    assert {r.outcome for r in await sync(reg, specs)} == {"unchanged"}
    assert all(r.status is AgentStatus.DRAFT for r in await reg.list())
    record = await reg.get("invoice-triage")
    assert record.owner == "finance-platform"
    assert record.card["tools"] == list(record.tools)


async def test_editing_a_spec_without_a_new_version_is_refused() -> None:
    reg = InMemoryAgentRegistry()
    spec = load_spec(HELLO)
    await sync(reg, [spec])
    edited = load_spec_text(HELLO.read_text().replace("Greets the caller.", "Greets everyone."))
    (result,) = await sync(reg, [edited])
    assert result.outcome.startswith("refused")
    assert "new version" in result.outcome


async def test_the_gate_allows_everything_in_dev_and_only_approved_in_staging() -> None:
    reg, audit = InMemoryAgentRegistry(), MemoryAuditSink()
    spec = load_spec(HELLO)
    assert await ApprovedOnly(reg, "dev").refusal(spec) is None
    staging = ApprovedOnly(reg, "staging", audit)
    assert "not in the registry" in (await staging.refusal(spec, ctx()) or "")
    await reg.register(record_of(spec))
    assert "is draft" in (await staging.refusal(spec, ctx()) or "")
    soon = utcnow() + timedelta(milliseconds=1)
    await reg.set_status(spec.name, spec.version, AgentStatus.APPROVED, by="carol", expires_at=soon)
    while utcnow() <= soon:
        pass
    assert "approval expired" in (await staging.refusal(spec, ctx()) or "")
    await reg.set_status(spec.name, spec.version, AgentStatus.DEPRECATED, by="carol")
    await reg.set_status(spec.name, spec.version, AgentStatus.APPROVED, by="carol")
    assert await staging.refusal(spec, ctx()) is None
    edited = load_spec_text(HELLO.read_text().replace("Greets the caller.", "Greets everyone."))
    assert "differs from the version" in (await staging.refusal(edited, ctx()) or "")
    refusals = [r.event.payload["reason"] for r in await audit.read()]
    assert len(refusals) == 4


async def test_deprecated_and_retired_agents_are_refused_in_staging_and_prod() -> None:
    """ASI10: taking a rogue agent out of service in the registry stops new runs of it."""
    reg, audit = InMemoryAgentRegistry(), MemoryAuditSink()
    spec = load_spec(HELLO)
    await reg.register(record_of(spec))
    await reg.set_status(spec.name, spec.version, AgentStatus.APPROVED, by="carol")
    for env in ("staging", "prod"):
        assert await ApprovedOnly(reg, env, audit).refusal(spec, ctx()) is None
    await reg.set_status(spec.name, spec.version, AgentStatus.DEPRECATED, by="carol")
    for env in ("staging", "prod"):
        assert "is deprecated" in (await ApprovedOnly(reg, env, audit).refusal(spec, ctx()) or "")
    await reg.set_status(spec.name, spec.version, AgentStatus.RETIRED, by="carol")
    for env in ("staging", "prod"):
        assert "is retired" in (await ApprovedOnly(reg, env, audit).refusal(spec, ctx()) or "")
    assert len(await audit.read()) == 4  # every refusal is audited
