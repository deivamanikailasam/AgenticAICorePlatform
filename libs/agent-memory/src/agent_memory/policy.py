"""What may be written to long-term memory, and in what form (Step 20).

``ValidatingMemoryPolicy`` (default: validate + redact + provenance) refuses, in order:

1. items with no provenance source;
2. untrusted content (tool results, retrieved documents, other agents) not yet validated;
3. procedural memory (learned instructions) unless it comes from the platform, or from a user
   who confirmed it: never from untrusted content, validated or not (OWASP ASI06);
4. content that the injection guardrails flag, unless the platform itself wrote it;
5. items below the minimum confidence.

It then redacts personal data with the PII guardrails and caps the item's lifetime (TTL per
memory kind, never beyond the maximum). The guardrails default to the platform's baseline
checks; Step 27 swaps in Presidio and the injection classifier through the ``guardrail`` slot.

``HumanCuratedMemoryPolicy`` stores only what a person has curated and queues every other
proposal for review.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta

from agent_contracts.dummies.safety import KeywordInjectionGuardrail, RegexPiiGuardrail
from agent_contracts.interfaces import Guardrail, MemoryPolicy
from agent_contracts.models import (
    GuardrailAction,
    GuardrailInput,
    GuardrailStage,
    MemoryItem,
    MemoryKind,
    MemoryWriteDecision,
    Trust,
    utcnow,
)

DEFAULT_TTL: Mapping[MemoryKind, timedelta | None] = {
    MemoryKind.SEMANTIC: timedelta(days=365),
    MemoryKind.EPISODIC: timedelta(days=90),
    MemoryKind.PROCEDURAL: None,  # kept until replaced by a newer version or erased
}


def refuse(*reasons: str) -> MemoryWriteDecision:
    return MemoryWriteDecision(allowed=False, reasons=reasons)


class ValidatingMemoryPolicy(MemoryPolicy):
    component_status = "implemented"

    def __init__(
        self,
        pii: Sequence[Guardrail] | None = None,
        injection: Sequence[Guardrail] | None = None,
        min_confidence: float = 0.5,
        ttl: Mapping[MemoryKind, timedelta | None] | None = None,
        max_ttl: timedelta = timedelta(days=730),
    ) -> None:
        self.pii = list(pii) if pii is not None else [RegexPiiGuardrail()]
        self.injection = list(injection) if injection is not None else [KeywordInjectionGuardrail()]
        self.min_confidence = min_confidence
        self.ttl = {**DEFAULT_TTL, **(ttl or {})}
        self.max_ttl = max_ttl

    async def evaluate_write(self, item: MemoryItem) -> MemoryWriteDecision:
        prov = item.provenance
        if not prov.source.strip():
            return refuse("every memory needs a provenance source")
        if prov.trust is Trust.UNTRUSTED and not prov.validated:
            return refuse("untrusted content must be validated before it is remembered")
        if item.kind is MemoryKind.PROCEDURAL and not (
            prov.trust is Trust.TRUSTED or (prov.trust is Trust.USER and prov.validated)
        ):
            return refuse(
                "instructions are learned only from the platform or a user who confirmed them"
            )
        if item.kind is MemoryKind.PROCEDURAL and not item.key:
            return refuse("procedural memory needs a key so it can be versioned")
        if prov.trust is not Trust.TRUSTED:
            for guard in self.injection:
                verdict = await guard.check(
                    GuardrailInput(
                        stage=GuardrailStage.RETRIEVED, text=item.content, trust=prov.trust
                    )
                )
                if verdict.action is GuardrailAction.BLOCK:
                    return refuse("looks like an instruction injection", *verdict.findings)
        if item.confidence < self.min_confidence:
            return refuse(f"confidence {item.confidence} is below {self.min_confidence}")

        content, reasons = item.content, []
        for guard in self.pii:
            verdict = await guard.check(
                GuardrailInput(stage=GuardrailStage.OUTPUT, text=content, trust=prov.trust)
            )
            if verdict.action is GuardrailAction.REDACT and verdict.redacted_text is not None:
                content = verdict.redacted_text
                reasons.append(
                    f"redacted personal data ({', '.join(sorted(set(verdict.findings)))})"
                )
        now = utcnow()
        default = self.ttl.get(item.kind)
        limit = now + self.max_ttl
        expires = item.expires_at or (now + default if default else None)
        if item.kind is not MemoryKind.PROCEDURAL:
            expires = min(expires or limit, limit)
        return MemoryWriteDecision(
            allowed=True,
            item=item.model_copy(update={"content": content, "expires_at": expires}),
            reasons=tuple(reasons),
        )


class HumanCuratedMemoryPolicy(MemoryPolicy):
    """Only items a person curated (``source="human:<id>"``, validated) are stored; every other
    write is refused and kept in ``proposals`` for a reviewer."""

    component_status = "implemented"

    def __init__(self, inner: MemoryPolicy | None = None) -> None:
        self.inner = inner or ValidatingMemoryPolicy()
        self.proposals: list[MemoryItem] = []

    async def evaluate_write(self, item: MemoryItem) -> MemoryWriteDecision:
        if item.provenance.source.startswith("human:") and item.provenance.validated:
            return await self.inner.evaluate_write(
                item.model_copy(
                    update={
                        "provenance": item.provenance.model_copy(update={"trust": Trust.TRUSTED})
                    }
                )
            )
        checked = await self.inner.evaluate_write(item)
        if checked.allowed and checked.item is not None:
            self.proposals.append(checked.item)
            return refuse("queued for human curation")
        return checked
