"""Step 29 "Done when", end to end through the run API against OpenBao and Postgres (ASI03):

* a tool call's audit record names the user, the agent and the credential lease;
* the lease is revoked when the call ends (and a leaked one expires within minutes: see
  ``libs/agent-tools/tests/test_openbao.py``);
* no secret appears in any prompt, run event, audit record or trace.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import psycopg
import pytest
from agent_api.app import build_app
from agent_api.assembly import Assembly
from agent_api.runs import RunManager
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import ModelClient
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Credential,
    Message,
    ModelCapabilities,
    Role,
    RunContext,
    ToolCall,
)
from agent_spec import load_spec_text
from agent_spec.platform import load_platform
from agent_tools.openbao import OpenBaoCredentialBroker, TokenAuth
from mcp.server.auth.provider import AccessToken
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

pytestmark = pytest.mark.integration

BAO = os.environ.get("P0_OPENBAO_URL", "http://127.0.0.1:8200")
ADMIN = os.environ.get("OPENBAO_ADMIN_TOKEN", "standin-only")
APP_DSN = os.environ.get("AGENT_DB_DSN", "postgresql://agent_app:standin-only@127.0.0.1:5432/agent")
ADMIN_DSN = os.environ.get(
    "P0_POSTGRES_ADMIN_DSN", "postgresql://postgres:standin-only@127.0.0.1:5432/postgres"
)

SPEC = """
spec_version: 1
name: supplier-check
version: 0.1.0
owner: platform
description: Looks suppliers up in the database with a short-lived credential.
risk_tier: low
autonomy: hybrid
pattern: react
runtime: { adapter: langgraph, durability: memory }
budgets: { max_steps: 10, max_wall_seconds: 60, max_tokens: 100000, max_cost_aud: 1.0 }
tools:
  - ref: local://platform/sql_read
    credentials: agent-db/creds/tool-reader
    config:
      database: agent
      queries:
        supplier_by_name: "select name, abn, status from tool_data.suppliers where name = %(name)s"
"""


class Scripted(ModelClient):
    """Looks the supplier up, then answers from the tool result. Keeps every request."""

    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(tool_calling=True)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if not any(m.role is Role.TOOL for m in request.messages):
            call = ToolCall(
                name="sql_read",
                arguments={"query": "supplier_by_name", "params": {"name": "Acme Pty Ltd"}},
            )
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, tool_calls=(call,)), model=request.model
            )
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content="Acme Pty Ltd is an active supplier."),
            model=request.model,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        yield ChatChunk(delta="")


class Spy(OpenBaoCredentialBroker):
    """The real broker; also keeps what it issued, so the test knows which values to hunt."""

    issued: list[Credential]

    async def issue(self, source: str, ctx: RunContext) -> Credential:
        cred = await super().issue(source, ctx)
        self.issued.append(cred)
        return cred


class Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        return AccessToken(
            token=token,
            client_id="t",
            scopes=[],
            expires_at=None,
            claims={"preferred_username": "alice", "groups": ["/acme/finance"]},
        )


@pytest.fixture
async def suppliers() -> AsyncIterator[None]:
    async with await psycopg.AsyncConnection.connect(APP_DSN, autocommit=True) as conn:
        await conn.execute(
            "create table if not exists tool_data.suppliers (name text primary key, abn text,"
            " status text)"
        )
        await conn.execute(
            "insert into tool_data.suppliers values ('Acme Pty Ltd', '51 824 753 556', 'active')"
            " on conflict do nothing"
        )
    yield
    async with await psycopg.AsyncConnection.connect(APP_DSN, autocommit=True) as conn:
        await conn.execute("drop table if exists tool_data.suppliers")


async def test_credentials_are_leased_per_call_audited_and_never_seen(
    suppliers: None, span_exporter: InMemorySpanExporter
) -> None:
    spec = load_spec_text(SPEC)
    model = Scripted()
    broker = Spy(BAO, TokenAuth(ADMIN))
    broker.issued = []
    audit = MemoryAuditSink()

    async def no_memory(s: Any) -> None:
        return None

    assembly = Assembly(
        load_platform(),
        RulesPolicyDecisionPoint(),
        audit,
        model_factory=lambda s: model,
        memory_factory=no_memory,
        broker=broker,
    )
    manager = RunManager({spec.name: spec}, assembly)
    app = build_app(manager, Verifier())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/v1/agents/supplier-check/runs",
            json={"input": {"task": "Is Acme Pty Ltd an active supplier?"}},
            headers={"Authorization": "Bearer t"},
        )
        assert resp.status_code == 201, resp.text
        run = manager.runs[resp.json()["run_id"]]
        await manager.wait_idle(run)
        record = (await c.get(f"/v1/runs/{run.run_id}", headers={"Authorization": "Bearer t"})).text
    assert run.status == "succeeded", run.events[-1]

    # the tool really ran with the credential: its rows reached the model
    (cred,) = broker.issued
    assert "51 824 753 556" in model.requests[-1].model_dump_json()

    # the audit record names user, agent and lease, and the lease was revoked
    calls = [r.event for r in await audit.read(run.run_id) if r.event.type == "tool.call"]
    (call,) = calls
    assert call.ctx.user_id == "alice"
    assert call.ctx.agent_id == "supplier-check"
    assert call.payload["credential"]["lease_id"] == cred.lease_id
    assert call.payload["credential"]["source"] == "openbao:agent-db/creds/tool-reader"
    assert call.payload["credential"]["revoked"] is True
    assert not await broker.valid(cred)
    async with await psycopg.AsyncConnection.connect(ADMIN_DSN, autocommit=True) as conn:
        gone = await (
            await conn.execute(
                "select count(*) from pg_roles where rolname = %s", (cred.secret("username"),)
            )
        ).fetchone()
    assert gone == (0,), "OpenBao dropped the database user when the lease was revoked"

    # no secret anywhere: prompts, run events, run record, audit records, the run's spans
    spans = span_exporter.get_finished_spans()
    names = {s.name for s in spans}
    assert {"invoke_agent supplier-check", "execute_tool sql_read"} <= names  # Step 30
    haystacks = {
        "spans": json.dumps(
            [
                {
                    "name": s.name,
                    "attributes": dict(s.attributes or {}),
                    "events": [dict(e.attributes or {}) for e in s.events],
                }
                for s in spans
            ],
            default=str,
        ),
        "prompts": json.dumps([r.model_dump(mode="json") for r in model.requests]),
        "events": json.dumps(run.events, default=str),
        "run record": record,
        "audit": json.dumps(
            [r.model_dump(mode="json") for r in await audit.read(run.run_id)], default=str
        ),
    }
    for value in (cred.secret("username"), cred.secret("password")):
        leaks = [name for name, text in haystacks.items() if value in text]
        assert not leaks, f"credential value found in {leaks}"
    await broker.aclose()


async def test_no_secret_in_the_traces_of_a_traced_run(suppliers: None) -> None:
    """The traced runner (``just run``, Step 4) with the same authorising executor and broker:
    every span's name, attributes and events are free of the credential's values. (The run API's
    own spans are checked in the test above.)"""
    from agent_compiler import compile_spec, tracing
    from agent_compiler.run import run as run_spec
    from agent_tools.assemble import grants_for
    from agent_tools.authorising import AuthorisingToolExecutor
    from agent_tools.executor import PlatformToolExecutor
    from agent_tools.registry import InMemoryToolRegistry
    from agent_tools.sql_read import SqlReadTool

    spec = load_spec_text(SPEC)
    reg = InMemoryToolRegistry()
    await reg.register(SqlReadTool(queries=spec.tools[0].config["queries"]))
    broker = Spy(BAO, TokenAuth(ADMIN))
    broker.issued = []
    audit = MemoryAuditSink()
    tools = AuthorisingToolExecutor(
        PlatformToolExecutor(reg),
        reg,
        RulesPolicyDecisionPoint(),
        audit,
        grants=grants_for(compile_spec(spec)),
        broker=broker,
    )
    exporter = InMemorySpanExporter()
    provider = tracing.configure(exporter=exporter)
    model = Scripted()
    ctx = RunContext(tenant_id="acme", user_id="alice", agent_id=spec.name)
    result, _ = await run_spec(
        spec,
        {"task": "Is Acme Pty Ltd an active supplier?"},
        ctx,
        provider,
        model=model,
        tools=tools,
    )
    assert result.status.value == "succeeded", result
    (cred,) = broker.issued
    spans = exporter.get_finished_spans()
    assert len(spans) >= 2, "a root span and node spans"
    text = json.dumps(
        [
            {
                "name": s.name,
                "attributes": dict(s.attributes or {}),
                "events": [dict(e.attributes or {}) for e in s.events],
            }
            for s in spans
        ],
        default=str,
    )
    assert "supplier-check" in text
    for value in (cred.secret("username"), cred.secret("password")):
        assert value not in text
        assert value not in json.dumps([r.model_dump(mode="json") for r in model.requests])
    assert not await broker.valid(cred)
    await broker.aclose()
