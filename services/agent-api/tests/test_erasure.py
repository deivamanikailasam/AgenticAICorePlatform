"""Right to erasure through the run API (Step 20): one request removes a user's runs, event
logs, checkpoint threads and long-term memories, and nothing of anyone else's."""

from typing import Any

import httpx
from agent_api.app import build_app
from agent_api.assembly import Assembly
from agent_api.runs import RunManager
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.dummies.state import InMemoryCheckpointer, InMemoryMemoryStore
from agent_contracts.models import MemoryScope, Message, Role, ToolCall, Trust
from agent_contracts.testing import ctx
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


async def test_one_request_erases_a_user_everywhere_and_nobody_else() -> None:
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
    app = build_app(manager, Verifier())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        runs = {}
        for who in ("t-alice", "t-carol"):
            resp = await c.post(
                "/v1/agents/assistant/runs", json={"input": {"message": "hi"}}, headers=auth(who)
            )
            assert resp.status_code == 201, resp.text
            runs[who] = resp.json()
            await manager.wait_idle(manager.runs[runs[who]["run_id"]])
        # a tenant-wide fact learned from alice: it is about her, so it goes too
        await memory.remember(
            ctx(user_id="alice", tenant="acme"),
            "Alice's team closes books on Fridays.",
            source="user",
            trust=Trust.USER,
            level="tenant",
        )
        alice_thread = runs["t-alice"]["thread_id"]
        assert await checkpointer.list("acme", alice_thread)

        for who in ("t-alice", "t-gina"):  # not a privacy officer / another tenant's
            body = {"user": "alice", "tenant": "acme"}
            denied = await c.post("/v1/admin/erasure", json=body, headers=auth(who))
            assert denied.status_code == 403
        resp = await c.post("/v1/admin/erasure", json={"user": "alice"}, headers=auth("t-pat"))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"erased": {"runs": 1, "threads": 1, "memories": 3}}

        assert (
            await c.get(f"/v1/runs/{runs['t-alice']['run_id']}", headers=auth("t-pat"))
        ).status_code == 404
        assert not await checkpointer.list("acme", alice_thread)
        assert (
            await c.get(f"/v1/runs/{runs['t-carol']['run_id']}", headers=auth("t-carol"))
        ).status_code == 200

    tenant = MemoryScope(tenant_id="acme")
    left_alice = await store.search(
        MemoryScope(tenant_id="acme", user_id="alice"), "Alice prefers post Fridays"
    )
    assert left_alice == []
    carol = await store.search(
        MemoryScope(tenant_id="acme", user_id="carol"), "Carol prefers e-mail"
    )
    assert carol
    assert await store.search(tenant, "closes books Fridays") == []
    erased = [r.event for r in await audit.read() if r.event.type == "subject.erased"]
    assert [e.payload["subject"] for e in erased] == ["alice"]
    assert erased[0].payload["requested_by"] == "pat"
