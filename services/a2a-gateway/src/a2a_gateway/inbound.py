"""Who may call a published agent: a verified Keycloak token, then policy.

Every A2A request for a published agent (except fetching its Agent Card, which is public so
callers can discover how to authenticate) needs a bearer token issued by the platform's Keycloak
for the platform audience. A missing or invalid token is 401; then OPA decides ``a2a.inbound``
with ``tool`` = the agent and ``allowed_tools`` = the published agents (so the agents' kill
switch applies); a denial is 403. Each decision is audited.

What the remote caller then asks the agent to do is not trusted because it passed this gate: the
message becomes the run's user input, and every tool call inside the run is authorised by policy
on its own, as the calling user.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_contracts.interfaces import AuditSink, PolicyDecisionPoint
from agent_contracts.models import AuditEvent, PolicyEffect, PolicyInput, RunContext, SideEffect
from agent_tools.auth import tenant_of


class TokenVerifier(Protocol):
    async def verify_token(self, token: str) -> Any: ...


@dataclass(frozen=True)
class Admission:
    status: int  # 200 admitted, 401 no valid token, 403 policy denied, 404 not published
    reason: str = ""
    subject: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == 200


class Inbound:
    def __init__(
        self,
        verifier: TokenVerifier,
        pdp: PolicyDecisionPoint,
        audit: AuditSink,
        published: Sequence[str],
    ) -> None:
        self.verifier = verifier
        self.pdp = pdp
        self.audit = audit
        self.published = tuple(published)

    async def admit(self, authorization: str | None, agent: str) -> Admission:
        if agent not in self.published:
            return Admission(404, f"no published agent {agent!r}")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return Admission(401, "a bearer token is required")
        access = await self.verifier.verify_token(token)
        if access is None:
            return Admission(401, "invalid or expired token")
        claims = access.claims or {}
        groups = [str(g) for g in claims.get("groups", [])]
        user = str(claims.get("preferred_username") or access.subject or "unknown")
        try:
            tenant = tenant_of(groups)
        except PermissionError as exc:
            return Admission(403, str(exc))
        subject = {
            "authenticated": True,
            "user": user,
            "client_id": access.client_id,
            "groups": groups,
            "roles": [str(r) for r in claims.get("realm_access", {}).get("roles", [])],
        }
        ctx = RunContext(tenant_id=tenant, user_id=user, agent_id=f"a2a-client:{access.client_id}")
        decision = await self.pdp.decide(
            PolicyInput(
                action="a2a.inbound",
                ctx=ctx,
                tool=agent,
                tool_ref=f"agent://{agent}",
                side_effect=SideEffect.READ,  # the run's own tool calls are authorised one by one
                subject=subject,
                tool_tags=("a2a",),
                allowed_tools=self.published,
            )
        )
        await self.audit.append(
            AuditEvent(
                type="a2a.inbound",
                ctx=ctx,
                payload={
                    "agent": agent,
                    "client_id": access.client_id,
                    "effect": decision.effect.value,
                    "reasons": list(decision.reasons),
                },
            )
        )
        if decision.effect is not PolicyEffect.ALLOW:
            return Admission(403, "; ".join(decision.reasons) or "denied by policy", subject)
        return Admission(200, subject=subject)
