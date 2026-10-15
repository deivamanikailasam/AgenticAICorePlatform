"""The memory service: the only way runs read and write long-term memory (Step 20).

Every write goes through the memory policy, with provenance stamped by the platform from the run
(source, trust, run id, whose data it is), never taken from the model. Every decision, allowed or
refused, is audited as ``memory.write`` with a hash of the content rather than the content.

Scope levels (``memory.long_term.scope`` in the spec):

* ``user`` (default): the run's user only;
* ``tenant``: every user of the tenant; items learned in a user's run record that user as their
  subject, so erasing the user removes them too;
* ``agent``: this agent only, for the run's user.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import timedelta
from typing import Literal

from agent_contracts.interfaces import AuditSink, MemoryPolicy, MemoryStore
from agent_contracts.models import (
    AuditEvent,
    MemoryItem,
    MemoryKind,
    MemoryScope,
    MemoryWriteDecision,
    Message,
    Provenance,
    Role,
    RunContext,
    Trust,
    utcnow,
)

Level = Literal["user", "tenant", "agent"]


class MemoryService:
    def __init__(
        self,
        store: MemoryStore,
        policy: MemoryPolicy,
        *,
        audit: AuditSink | None = None,
        level: Level = "user",
        k: int = 5,
        ttl_days: int | None = None,
    ) -> None:
        self.store = store
        self.policy = policy
        self.audit = audit
        self.level = level
        self.k = k
        self.ttl = timedelta(days=ttl_days) if ttl_days else None

    def scope(self, ctx: RunContext, level: Level | None = None) -> MemoryScope:
        lv = level or self.level
        return MemoryScope(
            tenant_id=ctx.tenant_id,
            user_id=None if lv == "tenant" else ctx.user_id,
            agent_id=ctx.agent_id if lv == "agent" else None,
        )

    async def remember(
        self,
        ctx: RunContext,
        content: str,
        *,
        source: str,
        trust: Trust,
        validated: bool = False,
        kind: MemoryKind = MemoryKind.SEMANTIC,
        key: str | None = None,
        confidence: float = 1.0,
        level: Level | None = None,
    ) -> tuple[str | None, MemoryWriteDecision]:
        scope = self.scope(ctx, level)
        item = MemoryItem(
            scope=scope,
            kind=kind,
            key=key,
            content=content,
            confidence=confidence,
            expires_at=utcnow() + self.ttl if self.ttl else None,
            provenance=Provenance(
                source=source,
                trust=trust,
                run_id=ctx.run_id,
                validated=validated,
                subject_user_id=ctx.user_id if scope.user_id is None else None,
            ),
        )
        decision = await self.policy.evaluate_write(item)
        stored = None
        if decision.allowed and decision.item is not None:
            stored = await self.store.put(decision.item)
        await self._audit(ctx, item, decision, stored)
        return stored, decision

    async def _audit(
        self, ctx: RunContext, item: MemoryItem, decision: MemoryWriteDecision, stored: str | None
    ) -> None:
        if self.audit is None:
            return
        await self.audit.append(
            AuditEvent(
                type="memory.write",
                ctx=ctx,
                payload={
                    "allowed": decision.allowed,
                    "reasons": list(decision.reasons),
                    "item_id": stored,
                    "kind": item.kind.value,
                    "key": item.key,
                    "source": item.provenance.source,
                    "trust": item.provenance.trust.value,
                    "validated": item.provenance.validated,
                    "content_sha256": hashlib.sha256(item.content.encode()).hexdigest(),
                },
            )
        )

    async def search(
        self, ctx: RunContext, query: str, kinds: Sequence[MemoryKind] | None = None
    ) -> list[MemoryItem]:
        # widest scope the run may see: its user's, its agent's and the tenant's shared items
        scope = MemoryScope(tenant_id=ctx.tenant_id, user_id=ctx.user_id, agent_id=ctx.agent_id)
        hits = await self.store.search(scope, query, k=self.k)
        return [h for h in hits if kinds is None or h.kind in kinds]

    @staticmethod
    def messages(items: Sequence[MemoryItem]) -> list[Message]:
        """Memories as context messages, each with the trust of its source: platform-authored
        ones as system text, the user's own as user text, and anything that came from untrusted
        content (validated) delimited as data, never as instructions."""
        out = []
        label = {"procedural": "instruction", "episodic": "past run", "semantic": "fact"}
        for i in items:
            trust = i.provenance.trust
            out.append(
                Message(
                    role=Role.SYSTEM if trust is Trust.TRUSTED else Role.USER,
                    name="memory",
                    content=f"Memory ({label[i.kind.value]}, from {i.provenance.source}): {i.content}",
                    trust=trust,
                )
            )
        return out

    # ------------------------------------------------------------ RunMemory (agent loop hooks)

    async def recall(self, ctx: RunContext, query: str) -> list[Message]:
        return self.messages(await self.search(ctx, query))

    async def episode(
        self, ctx: RunContext, task: str, answer: str | None, steps: int, untrusted: bool
    ) -> None:
        """Record the run. A run that saw untrusted content keeps only the user's task and the
        outcome, never the answer text, so nothing a document wrote is persisted."""
        if untrusted or answer is None:
            content = f"Task: {task[:500]} | finished in {steps} steps"
        else:
            content = f"Task: {task[:500]} | answer: {answer[:1000]}"
        await self.remember(
            ctx,
            content,
            source=f"agent:{ctx.agent_id}",
            trust=Trust.USER,
            kind=MemoryKind.EPISODIC,
            level="user",
        )

    # ------------------------------------------------------------ erasure

    async def erase_subject(self, tenant_id: str, user_id: str) -> int:
        return await self.store.erase_subject(tenant_id, user_id)
