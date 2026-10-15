"""Only approved agents run in staging and production (Step 25, ASI10).

``ApprovedOnly.refusal(spec)`` returns why an agent may not run here, or None. In ``dev`` it
always allows (people work on drafts); in ``staging`` and ``prod`` the exact spec version must be
in the registry, approved, unexpired, and registered with this spec's content. Refusals are
audited. The run API checks it before creating a run, and team runtimes before delegating.
"""

from __future__ import annotations

from agent_contracts.interfaces import AgentRegistry, AuditSink
from agent_contracts.models import AgentStatus, AuditEvent, RunContext
from agent_spec import AgentSpec

from agent_registry.sync import spec_hash

ENFORCED = frozenset({"staging", "prod", "production"})


class ApprovedOnly:
    def __init__(
        self, registry: AgentRegistry, environment: str, audit: AuditSink | None = None
    ) -> None:
        self.registry = registry
        self.environment = environment
        self.audit = audit

    @property
    def enforced(self) -> bool:
        return self.environment in ENFORCED

    async def refusal(self, spec: AgentSpec, ctx: RunContext | None = None) -> str | None:
        if not self.enforced:
            return None
        who = f"{spec.name}@{spec.version}"
        try:
            record = await self.registry.get(spec.name, spec.version)
        except KeyError:
            reason: str | None = f"agent {who} is not in the registry"
        else:
            if record.spec_hash != spec_hash(spec):
                reason = f"agent {who} differs from the version that was registered and reviewed"
            elif record.status is not AgentStatus.APPROVED:
                reason = (
                    f"agent {who} is {record.status.value}; only approved agents run in "
                    f"{self.environment}"
                )
            elif not record.runnable():
                reason = f"agent {who}: approval expired at {record.expires_at}"
            else:
                reason = None
        if reason and self.audit is not None and ctx is not None:
            await self.audit.append(
                AuditEvent(
                    type="registry.refused",
                    ctx=ctx,
                    payload={
                        "agent": spec.name,
                        "version": spec.version,
                        "reason": reason,
                        "environment": self.environment,
                    },
                )
            )
        return reason
