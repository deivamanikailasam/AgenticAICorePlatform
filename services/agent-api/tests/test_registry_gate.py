"""Step 25 "Done when": in staging (and prod) only approved agents run, and a supervisor cannot
delegate to a draft agent. The worker is refused before it is assembled, and the refusal is
audited."""

import importlib.util
from pathlib import Path
from typing import Any

import httpx
from agent_api.app import build_app
from agent_api.assembly import Assembly
from agent_api.runs import RunManager
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.registry import InMemoryAgentRegistry
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.models import AgentStatus
from agent_registry.gate import ApprovedOnly
from agent_registry.sync import load_all, sync
from agent_spec.platform import load_platform
from agent_tools.builtin import CalculatorTool, WordCountTool
from agent_tools.publish import PublishReportTool
from agent_tools.registry import InMemoryToolRegistry

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "team_api", Path(__file__).parent / "test_team_api.py"
)
assert _spec
assert _spec.loader
team_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(team_api)

AUTH = {"Authorization": "Bearer t"}


async def world(
    environment: str, approve: list[str]
) -> tuple[Any, Any, MemoryAuditSink, dict[str, Any]]:
    audit = MemoryAuditSink()
    registry = InMemoryAgentRegistry()
    specs = load_all(ROOT / "agents")
    await sync(registry, specs)
    versions = {s.name: s.version for s in specs}
    for name in approve:
        await registry.set_status(
            name, versions[name], AgentStatus.APPROVED, by="carol", reason="test"
        )
    assembled: dict[str, Any] = {}
    tools = {
        "search": team_api.Search,
        "calculator": CalculatorTool,
        "word_count": WordCountTool,
        "publish_report": PublishReportTool,
    }

    async def tool_registry(spec: Any) -> InMemoryToolRegistry:
        assembled[spec.name] = True
        reg = InMemoryToolRegistry()
        for t in spec.tools:
            await reg.register(tools[t.name]())
        return reg

    async def no_memory(spec: Any) -> None:
        return None

    assembly = Assembly(
        load_platform(),
        RulesPolicyDecisionPoint(),
        audit,
        model_factory=lambda spec: ScriptedModelClient(list(team_api.SCRIPTS[spec.name])),
        registry_factory=tool_registry,
        memory_factory=no_memory,
        admit=ApprovedOnly(registry, environment, audit).refusal,
    )
    manager = RunManager(RunManager.load_agents(ROOT / "agents"), assembly)
    return manager, build_app(manager, team_api.Verifier()), audit, assembled


async def start(app: Any, agent: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(
            f"/v1/agents/{agent}/runs", json={"input": {"task": "Solar growth?"}}, headers=AUTH
        )


async def test_a_supervisor_cannot_delegate_to_a_draft_agent_in_staging() -> None:
    approved = [
        "research-report",
        "research-report-team",
        "research-report-researcher",
        "research-report-analyst",
        "research-report-critic",
    ]
    manager, app, audit, assembled = await world("staging", approved)  # writer stays draft
    resp = await start(app, "research-report")
    assert resp.status_code == 201, resp.text
    run = manager.runs[resp.json()["run_id"]]
    await manager.wait_idle(run)
    # no report: the writer was never used, so the team stops, and nothing is published
    assert run.status in ("stopped", "failed"), run.status
    assert not run.interrupts, "no approval is asked for a report that was never written"
    assert "research-report-writer" not in assembled, "a refused worker is never built"
    delegations = [r.event.payload for r in await audit.read() if r.event.type == "agent.delegate"]
    # policy allowed the critic too, but with no report to read it was refused and never built
    assert [d["worker"] for d in delegations] == ["researcher", "analyst", "critic"]
    assert "research-report-critic" not in assembled
    refused = [r.event.payload for r in await audit.read() if r.event.type == "registry.refused"]
    assert refused == [
        {
            "agent": "research-report-writer",
            "version": "0.1.0",
            "reason": "agent research-report-writer@0.1.0 is draft; only approved agents run "
            "in staging",
            "environment": "staging",
        }
    ]


async def test_a_draft_agent_cannot_be_started_in_prod() -> None:
    _, app, _audit, assembled = await world("prod", [])
    resp = await start(app, "hello-workflow")
    assert resp.status_code == 403
    assert "hello-workflow@0.1.0 is draft" in resp.json()["error"]
    assert assembled == {}


async def test_drafts_run_in_dev() -> None:
    manager, app, _, assembled = await world("dev", [])
    resp = await start(app, "research-report")
    assert resp.status_code == 201
    run = manager.runs[resp.json()["run_id"]]
    await manager.wait_idle(run)
    # the team wrote and reviewed the report; publishing waits for a person
    assert run.status == "interrupted", run.events[-1]
    assert run.interrupts[0]["action"]["tool"] == "publish_report"
    assert "research-report-writer" in assembled
