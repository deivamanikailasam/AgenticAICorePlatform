"""``AuthorisingToolExecutor``: policy, rate limit and audit around the platform executor (Step 10).

For every call, in this order:
1. look the tool up (unknown tools are refused before policy, so nothing about them leaks);
2. ask the policy decision point, with who is calling (``current_caller``), the tenant, agent,
   tool, side effect, data class, arguments and risk tier; ``deny`` and ``require_approval`` stop
   here. A grant in the agent's spec can tighten ``allow`` to ``require_approval``, never loosen.
   A person's approval travels with the call as evidence; its binding to this tool and these
   arguments is verified here and policy decides whether it satisfies the approval (Step 15);
3. count the call against the tool's per-minute limit for this tenant and agent;
4. when the grant names a credential source, issue a short-lived credential for this one call
   from the credential broker (Step 29), hand it to the tool through ``current_credential``,
   and revoke it as soon as the call ends;
5. run it through the inner executor (validation, idempotency, timeouts, retries, spill);
6. audit the decision and the outcome, allowed or not, with the credential's lease (never its
   values) next to the user and agent the call ran for.

Tools that address tenant-owned data take a ``tenant_id`` argument; it is passed to policy as
``resource_tenant_id``, so a call for another tenant's data is denied and audited.
"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_contracts.interfaces import (
    AuditSink,
    CredentialBroker,
    PolicyDecisionPoint,
    ToolExecutor,
    ToolRegistry,
)
from agent_contracts.models import (
    AuditEvent,
    Credential,
    PolicyDecision,
    PolicyEffect,
    PolicyInput,
    RunContext,
    SideEffect,
    ToolCall,
    ToolErrorCode,
    ToolResult,
    arguments_digest,
)

from agent_tools.credentials import current_credential
from agent_tools.identity import acting_agent
from agent_tools.ratelimit import MemoryRateLimiter, RateLimiter

TENANT_ARGUMENT = "tenant_id"


@dataclass(frozen=True)
class Caller:
    """Who the current call acts for, as established by a verified token at the edge."""

    subject: Mapping[str, Any]
    via: str = "runtime"
    client_id: str | None = None


#: Set by the edge (MCP gateway, agent API, runtime) for the duration of one call.
current_caller: contextvars.ContextVar[Caller | None] = contextvars.ContextVar(
    "current_caller", default=None
)


@dataclass(frozen=True)
class ToolGrant:
    """What an agent's spec grants for one tool (``tools[]`` in agent.yaml)."""

    rate_limit_per_minute: int | None = None
    approval_required: bool = False
    approvers: str | None = None
    credentials: str | None = None  # credential source issued for each call (Step 29)


def approval_facts(call: ToolCall) -> dict[str, Any] | None:
    """What policy may rely on about a person's decision for this call. ``bound`` is verified
    here, by the platform: the evidence names this tool and the digest of these exact arguments,
    so it cannot be replayed for a different call."""
    ev = call.approval
    if ev is None:
        return None
    return {
        "approval_id": ev.approval_id,
        "approved": ev.approved,
        "approver": ev.approver,
        "approver_groups": list(ev.approver_groups),
        "co_approver": ev.co_approver,
        "co_approver_groups": list(ev.co_approver_groups),
        "edited": ev.edited,
        "bound": ev.tool == call.name and ev.arguments_digest == arguments_digest(call.arguments),
    }


@dataclass
class AuthorisingToolExecutor(ToolExecutor):
    inner: ToolExecutor
    registry: ToolRegistry
    pdp: PolicyDecisionPoint
    audit: AuditSink
    limiter: RateLimiter = field(default_factory=MemoryRateLimiter)
    #: tool name → grant. None means "not an agent" (a person calling through the gateway).
    grants: Mapping[str, ToolGrant] | None = None
    default_rate_limit_per_minute: int | None = None
    broker: CredentialBroker | None = None

    async def decide(self, call: ToolCall, ctx: RunContext) -> PolicyDecision:
        caller = current_caller.get()
        spec = (await self.registry.get(call.name)).spec
        resource_tenant = call.arguments.get(TENANT_ARGUMENT)
        decision = await self.pdp.decide(
            PolicyInput(
                ctx=ctx,
                tool=call.name,
                tool_ref=spec.ref,
                tool_tags=spec.tags,
                side_effect=spec.side_effect,
                data_classification=spec.data_classification,
                resource_tenant_id=str(resource_tenant) if resource_tenant is not None else None,
                arguments=dict(call.arguments),
                subject=dict(caller.subject) if caller else {},
                allowed_tools=tuple(self.grants) if self.grants is not None else None,
                approval=approval_facts(call),
            )
        )
        grant = (self.grants or {}).get(call.name)
        facts = approval_facts(call) or {}
        approved = bool(facts.get("approved") and facts.get("bound"))
        if (
            decision.effect is PolicyEffect.ALLOW
            and grant is not None
            and grant.approval_required
            and not approved
        ):
            return decision.model_copy(
                update={
                    "effect": PolicyEffect.REQUIRE_APPROVAL,
                    "reasons": (*decision.reasons, "the agent's spec requires approval"),
                    "approvers": grant.approvers or "group:approvers",
                }
            )
        return decision

    def _limit(self, name: str) -> int | None:
        grant = (self.grants or {}).get(name)
        if grant is not None and grant.rate_limit_per_minute is not None:
            return grant.rate_limit_per_minute
        return self.default_rate_limit_per_minute

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        started = time.perf_counter()
        caller = current_caller.get()
        payload: dict[str, Any] = {
            "via": caller.via if caller else "runtime",
            "client_id": caller.client_id if caller else None,
            "tool": call.name,
            "call_id": call.id,
        }
        if call.approval is not None:
            payload |= {
                "approval_id": call.approval.approval_id,
                "approver": call.approval.approver,
            }
        try:
            payload["ref"] = (await self.registry.get(call.name)).spec.ref
        except KeyError:
            return _fail(call, ToolErrorCode.NOT_FOUND, f"unknown tool {call.name}", started)

        decision = await self.decide(call, ctx)
        payload |= {
            "decision": decision.effect.value,
            "reasons": list(decision.reasons),
            "policy_version": decision.policy_version,
        }
        if decision.effect is not PolicyEffect.ALLOW:
            await self.audit.append(AuditEvent(type="policy.decision", ctx=ctx, payload=payload))
            if decision.effect is PolicyEffect.REQUIRE_APPROVAL:
                return _fail(
                    call,
                    ToolErrorCode.APPROVAL_REQUIRED,
                    f"approval required from {decision.approvers}: {'; '.join(decision.reasons)}",
                    started,
                )
            return _fail(
                call, ToolErrorCode.DENIED, f"denied: {'; '.join(decision.reasons)}", started
            )

        limit = self._limit(call.name)
        if limit is not None:
            rate = await self.limiter.hit(f"{ctx.tenant_id}:{ctx.agent_id}:{call.name}", limit)
            if not rate.allowed:
                payload |= {"ok": False, "error_code": ToolErrorCode.RATE_LIMITED.value}
                await self.audit.append(AuditEvent(type="tool.call", ctx=ctx, payload=payload))
                return _fail(
                    call,
                    ToolErrorCode.RATE_LIMITED,
                    f"rate limit {limit}/min reached; retry in {rate.retry_after_seconds}s",
                    started,
                )

        # downstream tokens are exchanged as this agent, not as the platform (Step 29)
        agent = acting_agent.set(ctx.agent_id) if self.grants is not None else None
        try:
            grant = (self.grants or {}).get(call.name)
            if grant is not None and grant.credentials:
                return await self._with_credential(call, ctx, grant.credentials, payload, started)
            result = await self.inner.execute(call, ctx)
        finally:
            if agent is not None:
                acting_agent.reset(agent)
        payload |= {
            "ok": result.ok,
            "error_code": result.error_code.value if result.error_code else None,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        await self.audit.append(AuditEvent(type="tool.call", ctx=ctx, payload=payload))
        return result

    async def _with_credential(
        self,
        call: ToolCall,
        ctx: RunContext,
        source: str,
        payload: dict[str, Any],
        started: float,
    ) -> ToolResult:
        """Issue → call → revoke. A credential that cannot be issued stops the call (fail
        closed); one that cannot be revoked still expires on its own, and the audit says so."""
        payload["credential_source"] = source
        if self.broker is None:
            code, message = ToolErrorCode.UNAVAILABLE, "no credential broker is configured"
        else:
            try:
                cred = await self.broker.issue(source, ctx)
            except (PermissionError, KeyError) as err:
                code, message = ToolErrorCode.DENIED, f"credential refused: {err}"
            except Exception as err:  # the secret manager is down: never run without
                code = ToolErrorCode.UNAVAILABLE
                message = f"credential unavailable: {type(err).__name__}"
            else:
                payload["credential"] = cred.lease()
                token = current_credential.set(cred)
                try:
                    result = await self.inner.execute(call, ctx)
                finally:
                    current_credential.reset(token)
                    try:
                        await self.broker.revoke(cred)
                        payload["credential"]["revoked"] = True
                    except Exception as err:
                        payload["credential"]["revoked"] = False
                        payload["credential"]["revoke_error"] = type(err).__name__
                if _leaks(result, cred):
                    # a tool that echoes its credential never hands it to the model
                    payload["credential"]["withheld_output"] = True
                    result = _fail(
                        call,
                        ToolErrorCode.FAILED,
                        "the tool's output contained its credential and was withheld",
                        started,
                    )
                payload |= {
                    "ok": result.ok,
                    "error_code": result.error_code.value if result.error_code else None,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                }
                await self.audit.append(AuditEvent(type="tool.call", ctx=ctx, payload=payload))
                return result
        payload |= {"ok": False, "error_code": code.value}
        await self.audit.append(AuditEvent(type="tool.call", ctx=ctx, payload=payload))
        return _fail(call, code, message, started)


def _leaks(result: ToolResult, cred: Credential) -> bool:
    shown = result.model_dump_json()
    return any(
        len(v) >= 6 and v in shown for v in (s.get_secret_value() for s in cred.values.values())
    )


def _fail(call: ToolCall, code: ToolErrorCode, message: str, started: float) -> ToolResult:
    return ToolResult(
        call_id=call.id,
        tool=call.name,
        ok=False,
        error_code=code,
        error=message,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


def delegation_authoriser(pdp: PolicyDecisionPoint, audit: AuditSink) -> Any:
    """Policy on delegation (Step 22, ASI07): may this team hand work to ``worker``? The same
    PDP as tool calls decides ``agent.delegate`` with the team's workers as its grants; every
    decision is audited."""

    async def authorise(worker: str, team: Sequence[str], ctx: RunContext) -> PolicyDecision:
        caller = current_caller.get()
        decision = await pdp.decide(
            PolicyInput(
                action="agent.delegate",
                ctx=ctx,
                tool=worker,
                tool_ref=f"agent://{worker}",
                side_effect=SideEffect.READ,
                subject=dict(caller.subject) if caller else {},
                allowed_tools=tuple(team),
            )
        )
        await audit.append(
            AuditEvent(
                type="agent.delegate",
                ctx=ctx,
                payload={
                    "worker": worker,
                    "effect": decision.effect.value,
                    "reasons": list(decision.reasons),
                },
            )
        )
        return decision

    return authorise
