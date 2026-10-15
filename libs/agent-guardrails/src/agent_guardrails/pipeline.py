"""Runs a spec's guardrails for one stage (Step 27).

Detectors run in parallel (``asyncio.gather``) to limit latency; guardrails that redact then
run one after another on the progressively redacted text, so their replacements do not fight.
Any block blocks. Every result that is not a clean pass is audited (``guardrail.result``) with
the findings, never the text itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_contracts.interfaces import AuditSink, Guardrail
from agent_contracts.models import (
    AuditEvent,
    GuardrailAction,
    GuardrailInput,
    GuardrailResult,
    GuardrailStage,
    RunContext,
    Trust,
)


@dataclass
class Outcome:
    blocked: bool
    text: str
    results: list[GuardrailResult] = field(default_factory=list)

    @property
    def findings(self) -> list[str]:
        return [f"{r.guardrail}: {f}" for r in self.results if not r.passed for f in r.findings]

    @property
    def reason(self) -> str:
        return "; ".join(self.findings)[:500]


class GuardrailPipeline:
    def __init__(self, guardrails: Sequence[Guardrail], audit: AuditSink | None = None) -> None:
        self.guardrails = list(guardrails)
        self.audit = audit

    def names(self) -> list[str]:
        return [g.name for g in self.guardrails]

    async def run(
        self,
        stage: GuardrailStage,
        text: str = "",
        *,
        data: Mapping[str, Any] | None = None,
        tool: str | None = None,
        trust: Trust = Trust.USER,
        ctx: RunContext | None = None,
    ) -> Outcome:
        active = [g for g in self.guardrails if stage in g.stages]
        if not active:
            return Outcome(False, text)
        detectors = [g for g in active if not getattr(g, "redacts", False)]
        redactors = [g for g in active if getattr(g, "redacts", False)]

        def payload(t: str) -> GuardrailInput:
            return GuardrailInput(
                stage=stage, text=t, data=dict(data or {}), tool=tool, trust=trust
            )

        results = list(await asyncio.gather(*(g.check(payload(text)) for g in detectors)))
        current = text
        for g in redactors:
            r = await g.check(payload(current))
            results.append(r)
            if r.action is GuardrailAction.REDACT and r.redacted_text is not None:
                current = r.redacted_text
        blocked = any(r.action is GuardrailAction.BLOCK for r in results)
        outcome = Outcome(blocked, current, results)
        if self.audit is not None and ctx is not None and outcome.findings:
            await self.audit.append(
                AuditEvent(
                    type="guardrail.result",
                    ctx=ctx,
                    payload={
                        "stage": stage.value,
                        "tool": tool,
                        "action": "block" if blocked else "redact",
                        "findings": outcome.findings,
                    },
                )
            )
        return outcome
