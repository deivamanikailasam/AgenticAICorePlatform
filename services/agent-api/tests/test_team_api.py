"""The research-report team through the run API (Step 22): workers are assembled like agents,
with their own tools and grants, and delegations are decided by policy and audited."""

import json
from pathlib import Path
from typing import Any

import httpx
from agent_api.app import build_app
from agent_api.assembly import Assembly
from agent_api.runs import RunManager
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import Tool
from agent_contracts.models import Message, Role, SideEffect, ToolCall, ToolSpec
from agent_spec.platform import load_platform
from agent_tools.builtin import CalculatorTool, WordCountTool
from agent_tools.registry import InMemoryToolRegistry
from mcp.server.auth.provider import AccessToken

ROOT = Path(__file__).resolve().parents[3]
SOURCES = [{"title": "Clean Energy Report 2026", "quote": "Rooftop solar capacity rose 18%."}]


class Search(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search",
            ref="mcp://rag-core/search",
            description="Search company documents.",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            side_effect=SideEffect.READ,
            idempotent=True,
        )

    async def invoke(self, arguments: Any, ctx: Any) -> Any:
        return {
            "results": [
                {"id": "d1", "snippet": SOURCES[0]["quote"], "citation": SOURCES[0]["title"]}
            ]
        }


class Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        return AccessToken(
            token=token,
            client_id="t",
            scopes=[],
            expires_at=None,
            claims={"preferred_username": "alice", "groups": ["/acme/research"]},
        )


def say(obj: Any) -> Message:
    return Message(role=Role.ASSISTANT, content=json.dumps(obj))


def call(name: str, **args: Any) -> Message:
    return Message(role=Role.ASSISTANT, tool_calls=(ToolCall(name=name, arguments=args),))


SCRIPTS = {
    "research-report": [
        say({"next": n, "task": n}) for n in ("researcher", "analyst", "writer", "FINISH")
    ],
    "research-report-researcher": [call("search", query="solar"), say({"sources": SOURCES})],
    "research-report-analyst": [say({"figures": {"growth_pct": 18}})],
    "research-report-writer": [
        say({"report": "Rooftop solar grew 18% last year (Clean Energy Report 2026)."})
    ],
}


async def test_the_team_runs_through_the_api_with_audited_delegations() -> None:
    audit = MemoryAuditSink()
    tools = {"search": Search, "calculator": CalculatorTool, "word_count": WordCountTool}
    registries: dict[str, list[str]] = {}

    async def registry(spec: Any) -> InMemoryToolRegistry:
        reg = InMemoryToolRegistry()
        for t in spec.tools:
            await reg.register(tools[t.name]())
        registries[spec.name] = [t.name for t in spec.tools]
        return reg

    async def no_memory(spec: Any) -> None:
        return None

    assembly = Assembly(
        load_platform(),
        RulesPolicyDecisionPoint(),
        audit,
        model_factory=lambda spec: ScriptedModelClient(list(SCRIPTS[spec.name])),
        registry_factory=registry,
        memory_factory=no_memory,
    )
    manager = RunManager(RunManager.load_agents(ROOT / "agents"), assembly)
    app = build_app(manager, Verifier())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/v1/agents/research-report/runs",
            json={"input": {"task": "How fast did rooftop solar grow in Victoria?"}},
            headers={"Authorization": "Bearer t"},
        )
        assert resp.status_code == 201, resp.text
        run = manager.runs[resp.json()["run_id"]]
        await manager.wait_idle(run)
        state = (
            await c.get(f"/v1/runs/{run.run_id}", headers={"Authorization": "Bearer t"})
        ).json()
    assert state["status"] == "succeeded", state
    assert registries == {
        "research-report": [],
        "research-report-researcher": ["search"],
        "research-report-analyst": ["calculator"],
        "research-report-writer": ["word_count"],
    }, "each worker is assembled with only its own tools"
    delegations = [r.event.payload for r in await audit.read() if r.event.type == "agent.delegate"]
    assert [d["worker"] for d in delegations] == ["researcher", "analyst", "writer"]
    assert all(d["effect"] == "allow" for d in delegations)
