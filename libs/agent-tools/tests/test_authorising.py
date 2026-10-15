import os
import uuid
from typing import Any

import pytest
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import PolicyDecisionPoint
from agent_contracts.models import (
    PolicyDecision,
    PolicyEffect,
    PolicyInput,
    RiskTier,
    RunContext,
    ToolCall,
    ToolErrorCode,
)
from agent_contracts.testing import ToolExecutorContract
from agent_tools.authorising import AuthorisingToolExecutor, Caller, ToolGrant, current_caller
from agent_tools.builtin import CalculatorTool, WordCountTool
from agent_tools.executor import PlatformToolExecutor
from agent_tools.ratelimit import MemoryRateLimiter, ValkeyRateLimiter
from agent_tools.registry import InMemoryToolRegistry

CTX = RunContext(tenant_id="acme", user_id="alice", agent_id="a1", risk_tier=RiskTier.MEDIUM)
ALICE = Caller(subject={"authenticated": True, "user": "alice", "groups": ["/acme/finance"]})


class _Recording(PolicyDecisionPoint):
    def __init__(self, effect: PolicyEffect = PolicyEffect.ALLOW) -> None:
        self.effect = effect
        self.seen: list[PolicyInput] = []

    async def decide(self, request: PolicyInput) -> PolicyDecision:
        self.seen.append(request)
        return PolicyDecision(effect=self.effect, reasons=("recorded",), policy_version="t")


class TestAuthorisingExecutorContract(ToolExecutorContract):
    """Wrapped around the platform executor with an allowing policy, it still meets the contract."""

    def make(self, registry: Any) -> AuthorisingToolExecutor:
        return AuthorisingToolExecutor(
            PlatformToolExecutor(registry), registry, RulesPolicyDecisionPoint(), MemoryAuditSink()
        )


async def _executor(
    pdp: PolicyDecisionPoint | None = None, **kw: Any
) -> tuple[AuthorisingToolExecutor, MemoryAuditSink]:
    reg = InMemoryToolRegistry()
    await reg.register(CalculatorTool())
    await reg.register(WordCountTool())
    audit = MemoryAuditSink()
    ex = AuthorisingToolExecutor(
        PlatformToolExecutor(reg), reg, pdp or RulesPolicyDecisionPoint(), audit, **kw
    )
    return ex, audit


async def test_allowed_call_runs_and_is_audited_with_the_decision() -> None:
    pdp = _Recording()
    ex, audit = await _executor(pdp)
    token = current_caller.set(ALICE)
    try:
        res = await ex.execute(ToolCall(name="calculator", arguments={"expression": "2+3"}), CTX)
    finally:
        current_caller.reset(token)
    assert res.ok
    sent = pdp.seen[0]
    assert sent.subject["user"] == "alice"
    assert sent.tool_ref == "local://platform/calculator"
    assert sent.ctx.risk_tier is RiskTier.MEDIUM
    assert sent.allowed_tools is None  # not an agent: a person through the gateway
    (record,) = await audit.read()
    assert record.event.type == "tool.call"
    assert record.event.payload["decision"] == "allow"
    assert record.event.payload["ok"] is True


async def test_other_tenants_data_is_denied_audited_and_never_runs() -> None:
    ex, audit = await _executor()
    call = ToolCall(name="word_count", arguments={"text": "x", "tenant_id": "globex"})
    res = await ex.execute(call, CTX)
    assert res.error_code is ToolErrorCode.DENIED
    assert "cross-tenant" in (res.error or "")
    (record,) = await audit.read()
    assert record.event.type == "policy.decision"
    assert record.event.payload["decision"] == "deny"
    assert "ok" not in record.event.payload


async def test_resource_tenant_is_lifted_from_the_arguments() -> None:
    pdp = _Recording()
    ex, _ = await _executor(pdp)
    await ex.execute(ToolCall(name="word_count", arguments={"text": "x", "tenant_id": "g"}), CTX)
    assert pdp.seen[0].resource_tenant_id == "g"


async def test_grants_become_allowed_tools() -> None:
    pdp = _Recording()
    ex, _ = await _executor(pdp, grants={"calculator": ToolGrant()})
    await ex.execute(ToolCall(name="calculator", arguments={"expression": "1"}), CTX)
    assert pdp.seen[0].allowed_tools == ("calculator",)


async def test_spec_approval_tightens_an_allow() -> None:
    grants = {"calculator": ToolGrant(approval_required=True, approvers="group:finance-approvers")}
    ex, audit = await _executor(_Recording(), grants=grants)
    res = await ex.execute(ToolCall(name="calculator", arguments={"expression": "1"}), CTX)
    assert res.error_code is ToolErrorCode.APPROVAL_REQUIRED
    assert "group:finance-approvers" in (res.error or "")
    assert (await audit.read())[0].event.payload["decision"] == "require_approval"


async def test_spec_cannot_loosen_a_deny() -> None:
    grants = {"calculator": ToolGrant(approval_required=False)}
    ex, _ = await _executor(_Recording(PolicyEffect.DENY), grants=grants)
    res = await ex.execute(ToolCall(name="calculator", arguments={"expression": "1"}), CTX)
    assert res.error_code is ToolErrorCode.DENIED


async def test_unknown_tool_is_refused_before_policy() -> None:
    pdp = _Recording()
    ex, _ = await _executor(pdp)
    res = await ex.execute(ToolCall(name="nope"), CTX)
    assert res.error_code is ToolErrorCode.NOT_FOUND
    assert pdp.seen == []


async def test_rate_limit_per_tool_and_agent() -> None:
    ex, audit = await _executor(grants={"calculator": ToolGrant(rate_limit_per_minute=2)})
    call = ToolCall(name="calculator", arguments={"expression": "1"})
    results = [await ex.execute(call, CTX) for _ in range(3)]
    assert [r.ok for r in results] == [True, True, False]
    assert results[2].error_code is ToolErrorCode.RATE_LIMITED
    other_agent = CTX.model_copy(update={"agent_id": "a2"})
    assert (await ex.execute(call, other_agent)).ok
    assert (await audit.read())[2].event.payload["error_code"] == "rate_limited"


async def test_default_rate_limit_applies_without_a_grant() -> None:
    ex, _ = await _executor(default_rate_limit_per_minute=1)
    call = ToolCall(name="calculator", arguments={"expression": "1"})
    assert (await ex.execute(call, CTX)).ok
    assert (await ex.execute(call, CTX)).error_code is ToolErrorCode.RATE_LIMITED


async def test_memory_limiter_resets_each_minute() -> None:
    now = [120.0]
    lim = MemoryRateLimiter(now=lambda: now[0])
    assert (await lim.hit("k", 1)).allowed
    blocked = await lim.hit("k", 1)
    assert not blocked.allowed
    assert blocked.retry_after_seconds == 60
    now[0] = 180.0
    assert (await lim.hit("k", 1)).allowed


@pytest.mark.integration
async def test_valkey_limiter_shares_the_count_between_instances() -> None:
    url = os.environ.get("P0_VALKEY_URL", "redis://127.0.0.1:6379/0")
    a, b = ValkeyRateLimiter(url), ValkeyRateLimiter(url)
    key = f"test:{uuid.uuid4().hex}"
    try:
        assert (await a.hit(key, 2)).allowed
        assert (await b.hit(key, 2)).allowed
        third = await a.hit(key, 2)
        assert not third.allowed
        assert third.count == 3
    finally:
        await a.aclose()
        await b.aclose()
