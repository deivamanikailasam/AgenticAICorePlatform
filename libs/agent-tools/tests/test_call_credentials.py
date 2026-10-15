"""Step 29 (ASI03): tools get short-lived credentials at call time, never from the prompt, the
arguments or the environment; each call's audit record names the user, the agent and the
credential lease; the credential is revoked when the call ends and never reaches the model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from agent_contracts.dummies.credentials import InMemoryCredentialBroker
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import Tool
from agent_contracts.models import (
    Credential,
    RunContext,
    ToolCall,
    ToolErrorCode,
    ToolSpec,
)
from agent_spec import SpecError, load_spec_text
from agent_tools.authorising import AuthorisingToolExecutor, ToolGrant
from agent_tools.credentials import current_credential, require_credential
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry

CTX = RunContext(tenant_id="acme", user_id="alice", agent_id="invoice-triage")
SOURCE = "db/tool-reader"


class Lookup(Tool):
    """Uses its credential; returns something that does not contain it."""

    def __init__(self, echo: bool = False) -> None:
        self.echo = echo
        self.seen: list[Credential] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="lookup", ref="local://platform/lookup")

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        cred = require_credential("lookup")
        self.seen.append(cred)
        if self.echo:  # a buggy tool that returns its connection string
            return {"dsn": f"postgresql://{cred.secret('username')}:{cred.secret('password')}@db"}
        return {"rows": 3}


class Failing(InMemoryCredentialBroker):
    async def issue(self, source: str, ctx: RunContext) -> Credential:
        raise ConnectionError("openbao unreachable")


async def _executor(
    tool: Tool, broker: Any, source: str | None = SOURCE
) -> tuple[AuthorisingToolExecutor, MemoryAuditSink]:
    reg = InMemoryToolRegistry()
    await reg.register(tool)
    audit = MemoryAuditSink()
    ex = AuthorisingToolExecutor(
        PlatformToolExecutor(reg),
        reg,
        RulesPolicyDecisionPoint(),
        audit,
        grants={"lookup": ToolGrant(credentials=source)},
        broker=broker,
    )
    return ex, audit


async def test_tool_gets_a_fresh_credential_per_call_and_it_is_revoked_after() -> None:
    broker = InMemoryCredentialBroker(sources=[SOURCE])
    tool = Lookup()
    ex, audit = await _executor(tool, broker)
    first = await ex.execute(ToolCall(name="lookup"), CTX)
    second = await ex.execute(ToolCall(name="lookup"), CTX)
    assert first.ok, first.error
    assert second.ok, second.error
    a, b = tool.seen
    assert a.lease_id != b.lease_id
    assert not await broker.valid(a)
    assert not await broker.valid(b)
    assert current_credential.get() is None  # only set for the duration of the call

    records = [r.event for r in await audit.read(CTX.run_id)]
    assert [r.type for r in records] == ["tool.call", "tool.call"]
    event = records[0]
    assert (event.ctx.user_id, event.ctx.agent_id) == ("alice", "invoice-triage")
    lease = event.payload["credential"]
    assert lease["lease_id"] == a.lease_id
    assert lease["source"] == SOURCE
    assert lease["issued_to"] == "invoice-triage"
    assert lease["revoked"] is True
    dumped = event.model_dump_json()
    for value in (a.secret("username"), a.secret("password")):
        assert value not in dumped


async def test_without_a_broker_the_call_fails_closed() -> None:
    tool = Lookup()
    ex, audit = await _executor(tool, None)
    result = await ex.execute(ToolCall(name="lookup"), CTX)
    assert result.error_code is ToolErrorCode.UNAVAILABLE
    assert not tool.seen
    (record,) = await audit.read(CTX.run_id)
    assert record.event.payload["credential_source"] == SOURCE


@pytest.mark.parametrize(
    ("broker", "code"),
    [
        (InMemoryCredentialBroker(sources=[], forbidden=[SOURCE]), ToolErrorCode.DENIED),
        (InMemoryCredentialBroker(sources=[]), ToolErrorCode.DENIED),
        (Failing(), ToolErrorCode.UNAVAILABLE),
    ],
)
async def test_refused_or_unavailable_credentials_stop_the_call(
    broker: Any, code: ToolErrorCode
) -> None:
    tool = Lookup()
    ex, _ = await _executor(tool, broker)
    result = await ex.execute(ToolCall(name="lookup"), CTX)
    assert result.error_code is code
    assert not tool.seen
    assert "openbao unreachable" not in (result.error or "")  # no internals to the model


async def test_a_tool_that_echoes_its_credential_never_hands_it_to_the_model() -> None:
    tool = Lookup(echo=True)
    ex, audit = await _executor(tool, InMemoryCredentialBroker(sources=[SOURCE]))
    result = await ex.execute(ToolCall(name="lookup"), CTX)
    (cred,) = tool.seen
    assert not result.ok
    assert "withheld" in (result.error or "")
    assert cred.secret("password") not in result.model_dump_json()
    (record,) = await audit.read(CTX.run_id)
    assert record.event.payload["credential"]["withheld_output"] is True


async def test_a_tool_without_a_grant_gets_no_credential() -> None:
    tool = Lookup()
    ex, _ = await _executor(tool, InMemoryCredentialBroker(sources=[SOURCE]), source=None)
    result = await ex.execute(ToolCall(name="lookup"), CTX)
    assert result.error_code is ToolErrorCode.FAILED
    assert "needs a call-time credential" in (result.error or "")


def test_the_spec_names_a_credential_source_never_a_secret() -> None:
    base = """
spec_version: 1
name: reader
version: 0.1.0
owner: platform
description: d
risk_tier: low
autonomy: hybrid
pattern: react
budgets: {{ max_steps: 5, max_wall_seconds: 30, max_tokens: 1000, max_cost_aud: 0.1 }}
tools:
  - ref: local://platform/sql_read
    credentials: {value}
"""
    spec = load_spec_text(base.format(value="agent-db/creds/tool-reader"))
    assert spec.tools[0].credentials == "agent-db/creds/tool-reader"
    for bad in ("postgresql://u:p@db/agent", "${secret:x#y}", "agent-db/static/x"):
        with pytest.raises(SpecError, match="credentials"):
            load_spec_text(base.format(value=f"'{bad}'"))
