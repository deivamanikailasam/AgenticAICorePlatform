"""Step 38: agent versions as config. A canary serves its share of threads; a failing candidate
is rolled back automatically by the online-eval gate; a shadow candidate never has a side
effect; feature flags switch an agent or a tool off."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from agent_api.app import build_app
from agent_api.assembly import Assembly
from agent_api.releases import (
    Candidate,
    Channel,
    Flags,
    Gate,
    Outcome,
    Releases,
    bucket,
    evaluate,
    load_releases,
)
from agent_api.runs import RunManager
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.models import Message, Role, SideEffect
from agent_spec import load_spec_text
from agent_spec.platform import load_platform
from agent_tools.builtin import CalculatorTool
from agent_tools.registry import InMemoryToolRegistry
from mcp.server.auth.provider import AccessToken

SPEC = """
spec_version: 1
name: ledger
version: {version}
owner: finance-platform
description: Adds up an amount and writes it to the ledger.
risk_tier: medium
autonomy: workflow
pattern: workflow
runtime: {{ adapter: langgraph, durability: memory }}
budgets: {{ max_steps: 10, max_wall_seconds: 30, max_tokens: 5000, max_cost_aud: 0.5 }}
tools:
  - ref: local://platform/calculator
  - ref: local://test/write_ledger
graph:
  nodes:
    - {{ id: total, type: tool, tool: calculator, config: {{ args: {{ expression: {expression} }} }} }}
    - {{ id: note, type: llm, config: {{ template: "Note the total ${{total.result}}." }} }}
    - {{ id: write, type: tool, tool: write_ledger, config: {{ args: {{ amount_aud: $total.result }} }} }}
  edges: ["total -> note: on_ok", "note -> write: on_ok"]
"""
STABLE = load_spec_text(SPEC.format(version="1.0.0", expression="$input.expression"))
# the candidate's change is broken: it sums a constant the calculator refuses
BROKEN = load_spec_text(SPEC.format(version="1.1.0", expression='"1 +"'))
GOOD = load_spec_text(SPEC.format(version="1.1.0", expression="$input.expression"))


class Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        roles = {"t-alice": [], "t-oscar": ["platform-operator"]}
        if token not in roles:
            return None
        return AccessToken(
            token=token,
            client_id="test-client",
            scopes=[],
            expires_at=None,
            claims={
                "preferred_username": token.removeprefix("t-"),
                "groups": ["/acme/finance"],
                "realm_access": {"roles": roles[token]},
            },
        )


class WriteLedger(CalculatorTool):
    def __init__(self, writes: list[dict[str, Any]]) -> None:
        self.writes = writes

    @property
    def spec(self) -> Any:
        return super().spec.model_copy(
            update={
                "name": "write_ledger",
                "ref": "local://test/write_ledger",
                "side_effect": SideEffect.WRITE,
                "input_schema": {
                    "type": "object",
                    "properties": {"amount_aud": {"type": "number"}},
                    "required": ["amount_aud"],
                },
                "output_schema": None,
            }
        )

    async def invoke(self, arguments: Any, ctx: Any) -> Any:
        self.writes.append({"amount_aud": arguments["amount_aud"], "version": ctx.agent_version})
        return {"written": True}


def api(
    candidate: Any, mode: str = "canary", weight: int = 10, **channel: Any
) -> tuple[RunManager, list[dict[str, Any]], MemoryAuditSink]:
    writes: list[dict[str, Any]] = []
    audit = MemoryAuditSink()

    async def registry(spec: Any) -> InMemoryToolRegistry:
        reg = InMemoryToolRegistry()
        await reg.register(CalculatorTool())
        await reg.register(WriteLedger(writes))
        return reg

    assembly = Assembly(
        load_platform(),
        RulesPolicyDecisionPoint(),
        audit,
        model_factory=lambda spec: ScriptedModelClient(
            [Message(role=Role.ASSISTANT, content="Noted.")] * 500
        ),
        registry_factory=registry,
    )
    versions = {("ledger", STABLE.version): STABLE}
    cand = None
    if candidate is not None:
        versions[("ledger", candidate.version)] = candidate
        cand = Candidate(candidate.version, mode, weight)
    releases = Releases({"ledger": Channel("ledger", STABLE.version, cand, **channel)}, versions)
    return RunManager({}, assembly, releases=releases), writes, audit


def client(manager: RunManager) -> httpx.AsyncClient:
    app = build_app(manager, Verifier())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")


async def run_once(c: httpx.AsyncClient, manager: RunManager, thread: str) -> dict[str, Any]:
    resp = await c.post(
        "/v1/agents/ledger/runs",
        json={"input": {"expression": "40+2"}, "thread_id": thread},
        headers={"Authorization": "Bearer t-alice"},
    )
    assert resp.status_code == 201, resp.text
    run = manager.runs[resp.json()["run_id"]]
    await manager.wait_idle(run)
    return {"version": run.ctx.agent_version, "status": run.status}


def threads(n: int, into_canary: bool) -> list[str]:
    """Thread ids the canary's hash puts inside (or outside) a 10% weight."""
    out, i = [], 0
    while len(out) < n:
        key = f"thread-{i}"
        if (bucket(key) < 10) == into_canary:
            out.append(key)
        i += 1
    return out


# ---------------------------------------------------------------- unit


def test_ten_percent_of_threads_go_to_the_canary_and_stay_there() -> None:
    share = sum(bucket(f"thread-{i}") < 10 for i in range(10_000)) / 10_000
    assert 0.09 < share < 0.11  # the hash spreads keys evenly (10,000 keys)
    assert bucket("thread-7") == bucket("thread-7"), "same thread, same version, every replica"


def test_the_gate_waits_then_passes_or_breaches_with_reasons() -> None:
    gate = Gate(min_runs=3)

    def runs(version: str, ok: list[bool], cost: float = 0.01) -> list[Outcome]:
        return [
            Outcome("a", version, f"{version}-{i}", "canary", o, cost, 1.0)
            for i, o in enumerate(ok)
        ]

    stable = runs("1", [True] * 10)
    assert evaluate(gate, stable, runs("2", [True, True])).decision == "waiting"
    assert evaluate(gate, stable, runs("2", [True] * 3)).decision == "pass"
    failing = evaluate(gate, stable, runs("2", [True, False, False]))
    assert failing.decision == "breach"
    assert "failure rate 66.67% vs 0.0%" in failing.reasons[0]
    pricey = evaluate(gate, stable, runs("2", [True] * 3, cost=0.02))
    assert pricey.decision == "breach"
    assert pricey.reasons == ("cost_aud +100.0% > 25.0% (0.02 vs 0.01)",)


def test_a_releases_file_must_name_deployed_versions_and_sane_weights() -> None:
    versions = {("ledger", "1.0.0"): STABLE, ("ledger", "1.1.0"): GOOD}
    ok = load_releases(
        {
            "agents": {
                "ledger": {"stable": "1.0.0", "candidate": {"version": "1.1.0", "weight": 10}}
            }
        },
        versions,
    )
    assert ok["ledger"].candidate == Candidate("1.1.0", "canary", 10)
    for bad, message in (
        ({"stable": "9.9.9"}, "not deployed"),
        ({"stable": "1.0.0", "candidate": {"version": "1.0.0"}}, "is the stable version"),
        ({"stable": "1.0.0", "candidate": {"version": "1.1.0", "weight": 0}}, "weight"),
        ({"stable": "1.0.0", "candidate": {"version": "1.1.0", "mode": "blue"}}, "mode"),
    ):
        with pytest.raises(ValueError, match=message):
            load_releases({"agents": {"ledger": bad}}, versions)


# ---------------------------------------------------------------- through the API


async def test_the_canary_serves_its_threads_and_a_thread_keeps_its_version() -> None:
    manager, writes, _ = api(GOOD)
    async with client(manager) as c:
        inside = [await run_once(c, manager, t) for t in threads(3, into_canary=True)]
        outside = [await run_once(c, manager, t) for t in threads(3, into_canary=False)]
        again = await run_once(c, manager, threads(1, into_canary=True)[0])
    assert {r["version"] for r in inside} == {"1.1.0"}
    assert {r["version"] for r in outside} == {"1.0.0"}
    assert again["version"] == "1.1.0", "a later run on the same thread stays on the canary"
    assert all(r["status"] == "succeeded" for r in inside + outside)
    assert len(writes) == 7


async def test_a_failing_canary_is_rolled_back_automatically_and_audited() -> None:
    manager, writes, audit = api(BROKEN, gate=Gate(min_runs=3))
    async with client(manager) as c:
        for t in threads(4, into_canary=False):
            assert (await run_once(c, manager, t))["status"] == "succeeded"
        served = [await run_once(c, manager, t) for t in threads(5, into_canary=True)]
    assert [r["version"] for r in served[:3]] == ["1.1.0"] * 3
    assert all(r["status"] == "failed" for r in served[:3])
    # the third failing run breached the gate: the canary's threads go to the stable version
    assert [r["version"] for r in served[3:]] == ["1.0.0"] * 2
    assert all(r["status"] == "succeeded" for r in served[3:])
    reason = await manager.releases.state.rolled_back("ledger", "1.1.0")
    assert reason is not None
    assert reason.startswith("failure rate 100.0% vs 0.0%")
    events = [r.event for r in await audit.read() if r.event.type == "release.rolled_back"]
    assert len(events) == 1
    assert events[0].payload["version"] == "1.1.0"
    async with client(manager) as c:
        refused = await c.get("/v1/admin/releases", headers={"Authorization": "Bearer t-alice"})
        shown = await c.get("/v1/admin/releases", headers={"Authorization": "Bearer t-oscar"})
    assert refused.status_code == 403
    report = shown.json()["releases"][0]
    assert report["candidate"]["rolled_back"] == reason
    assert report["online_eval"]["decision"] == "breach"
    assert report["online_eval"]["candidate_runs"] == 3
    assert {w["version"] for w in writes} == {"1.0.0"}, "the broken version wrote nothing"


async def test_a_shadow_candidate_runs_the_same_input_without_writing() -> None:
    manager, writes, audit = api(GOOD, mode="shadow", weight=100)
    async with client(manager) as c:
        served = await run_once(c, manager, "thread-a")
        for task in list(manager._shadows):
            await task
    assert served == {"version": "1.0.0", "status": "succeeded"}
    assert writes == [{"amount_aud": 42.0, "version": "1.0.0"}], "only the stable run wrote"
    shadows = [r.event for r in await audit.read() if r.event.type == "release.shadow_run"]
    assert len(shadows) == 1
    assert shadows[0].payload["version"] == "1.1.0"
    assert shadows[0].payload["status"] == "succeeded"
    assert len(manager.runs) == 1, "the caller sees one run"
    scored = await manager.releases.state.outcomes("ledger", "1.1.0", served_since())
    assert [o.mode for o in scored] == ["shadow"]


def served_since() -> Any:
    from datetime import UTC, datetime, timedelta

    return datetime.now(UTC) - timedelta(hours=1)


async def test_feature_flags_switch_an_agent_or_a_tool_off() -> None:
    manager, _, _ = api(None, flags=Flags(enabled=False))
    async with client(manager) as c:
        resp = await c.post(
            "/v1/agents/ledger/runs",
            json={"input": {"expression": "40+2"}},
            headers={"Authorization": "Bearer t-alice"},
        )
    assert resp.status_code == 403
    assert "switched off" in resp.json()["error"]
    manager, writes, _ = api(None, flags=Flags(disabled_tools=frozenset({"write_ledger"})))
    async with client(manager) as c:
        result = await run_once(c, manager, "thread-b")
    assert result["status"] == "failed"
    assert writes == [], "the switched-off tool was never called"


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_the_repository_releases_load_as_the_api_would(env: str) -> None:
    from pathlib import Path

    from agent_api.releases import MemoryReleaseState

    root = Path(__file__).resolve().parents[3]
    versions = RunManager.load_versions(root / "deploy" / "specs")
    releases = Releases.from_file(
        root / "deploy" / "releases" / f"{env}.yaml", versions, MemoryReleaseState()
    )
    agents = {p.name for p in (root / "agents").iterdir() if (p / "agent.yaml").exists()}
    assert set(releases.stable()) == agents
