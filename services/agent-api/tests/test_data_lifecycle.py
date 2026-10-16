"""Data lifecycle through the run API (Step 37): a person exports their own data (or a privacy
officer theirs), and retention prunes finished runs, old checkpoints and expired memories, never
a run still waiting for a person. Both are audited."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import yaml
from agent_api.app import LIFECYCLE, build_app
from agent_api.assembly import Assembly
from agent_api.runs import RunManager
from agent_api.store import MemoryRunStore
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.dummies.state import InMemoryCheckpointer, InMemoryMemoryStore
from agent_contracts.models import Message, Role, ToolCall
from agent_memory.policy import ValidatingMemoryPolicy
from agent_memory.service import MemoryService
from agent_spec import load_spec_text
from agent_spec.platform import load_platform
from agent_tools.registry import InMemoryToolRegistry
from mcp.server.auth.provider import AccessToken

USERS = {
    "t-alice": ("alice", ["/acme/finance"], []),
    "t-carol": ("carol", ["/acme/finance"], []),
    "t-pat": ("pat", ["/acme/privacy"], ["privacy-officer"]),
    "t-ops": ("ops", ["/acme/platform"], ["platform-operator"]),
    "t-gina": ("gina", ["/globex/privacy"], ["privacy-officer"]),
}
SPEC = """
spec_version: 1
name: assistant
version: 0.1.0
owner: platform
description: Remembers what users tell it.
risk_tier: low
autonomy: hybrid
pattern: hybrid
runtime: { adapter: langgraph, durability: checkpoint }
memory: { long_term: { store: memory, scope: user } }
budgets: { max_steps: 6, max_wall_seconds: 30, max_tokens: 5000, max_cost_aud: 0.1 }
tools:
  - ref: local://platform/remember
graph:
  nodes:
    - { id: assistant, type: agent, pattern: react, tools: [remember], max_iterations: 4 }
"""


class Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token not in USERS:
            return None
        user, groups, roles = USERS[token]
        return AccessToken(
            token=token,
            client_id="test-client",
            scopes=[],
            expires_at=None,
            claims={"preferred_username": user, "groups": groups, "realm_access": {"roles": roles}},
        )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def remembering(fact: str) -> ScriptedModelClient:
    return ScriptedModelClient(
        [
            Message(
                role=Role.ASSISTANT,
                tool_calls=(ToolCall(name="remember", arguments={"content": fact}),),
            ),
            Message(role=Role.ASSISTANT, content="Noted."),
        ]
    )


async def setup() -> tuple[RunManager, httpx.AsyncClient, MemoryService, MemoryAuditSink]:
    store, checkpointer, audit = InMemoryMemoryStore(), InMemoryCheckpointer(), MemoryAuditSink()
    memory = MemoryService(store, ValidatingMemoryPolicy(), audit=audit)
    facts = iter(["Alice prefers invoices by post.", "Carol prefers e-mail."])

    async def memory_factory(spec: Any) -> MemoryService:
        return memory

    async def registry(spec: Any) -> InMemoryToolRegistry:
        return InMemoryToolRegistry()

    assembly = Assembly(
        load_platform(),
        RulesPolicyDecisionPoint(),
        audit,
        model_factory=lambda spec: remembering(next(facts)),
        registry_factory=registry,
        checkpointer=checkpointer,
        memory_factory=memory_factory,
    )
    spec = load_spec_text(SPEC)
    manager = RunManager({spec.name: spec}, assembly)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_app(manager, Verifier())), base_url="http://t"
    )
    for who in ("t-alice", "t-carol"):
        resp = await client.post(
            "/v1/agents/assistant/runs", json={"input": {"message": "hi"}}, headers=auth(who)
        )
        assert resp.status_code == 201, resp.text
        await manager.wait_idle(manager.runs[resp.json()["run_id"]])
    return manager, client, memory, audit


async def test_a_person_exports_their_own_data_and_nobody_elses() -> None:
    _, c, _, audit = await setup()
    try:
        resp = await c.get("/v1/me/export", headers=auth("t-alice"))
        assert resp.status_code == 200, resp.text
        doc = resp.json()
        assert (doc["tenant"], doc["user"]) == ("acme", "alice")
        assert len(doc["runs"]) == 1
        assert doc["runs"][0]["events"]  # the transcript comes with the run
        assert len(doc["threads"]) == 1
        contents = [m["content"] for m in doc["memories"]["assistant"]]
        assert "Alice prefers invoices by post." in contents  # with the run's episode
        assert "Carol" not in json.dumps(doc)

        for who in ("t-carol", "t-gina"):  # another user; another tenant's privacy officer
            denied = await c.post(
                "/v1/admin/export", json={"user": "alice", "tenant": "acme"}, headers=auth(who)
            )
            assert denied.status_code == 403
        officer = await c.post("/v1/admin/export", json={"user": "alice"}, headers=auth("t-pat"))
        assert officer.status_code == 200
        assert len(officer.json()["runs"]) == 1
    finally:
        await c.aclose()
    exported = [r.event for r in await audit.read() if r.event.type == "subject.exported"]
    assert [(e.payload["subject"], e.payload["requested_by"]) for e in exported] == [
        ("alice", "alice"),
        ("alice", "pat"),
    ]
    assert exported[0].payload["memories"] == len(contents)


async def test_retention_prunes_finished_runs_but_never_a_run_waiting_for_a_person() -> None:
    manager, c, _, audit = await setup()
    store = manager.store
    assert isinstance(store, MemoryRunStore)
    old = (datetime.now(UTC) - timedelta(days=120)).isoformat(timespec="seconds")
    ids = list(store._runs)
    for rid in ids:
        store._runs[rid]["created_at"] = old
    store._runs[ids[1]]["status"] = "interrupted"  # e.g. waiting for an approval
    try:
        assert (await c.post("/v1/admin/retention", headers=auth("t-carol"))).status_code == 403
        resp = await c.post("/v1/admin/retention", headers=auth("t-ops"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["pruned"]["runs"] == 1
    finally:
        await c.aclose()
    left = await store.records()
    assert [r["run_id"] for r in left] == [ids[1]]
    assert await store.events(ids[0]) == []
    applied = [r.event for r in await audit.read() if r.event.type == "retention.applied"]
    assert applied[0].payload["runs"] == 1
    assert applied[0].payload["requested_by"] == "ops"


def test_the_retention_policy_is_complete() -> None:
    policy = yaml.safe_load(LIFECYCLE.read_text())
    assert {"runs", "checkpoints", "memory", "audit"} <= set(policy["retention"])
    assert "interrupted" not in policy["finished_statuses"]
    assert "running" not in policy["finished_statuses"]
