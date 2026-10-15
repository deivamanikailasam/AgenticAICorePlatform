from __future__ import annotations

import re
from collections.abc import Sequence

from agent_contracts.dummies._util import approx_tokens, mark_dummies
from agent_contracts.interfaces import ContextManager, MemoryPolicy
from agent_contracts.models import (
    ContextSection,
    MemoryItem,
    MemoryWriteDecision,
    Message,
    Role,
    Trust,
)

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\+?\d[\d\s-]{7,}\d")


class ValidateRedactMemoryPolicy(MemoryPolicy):
    """Refuses unvalidated untrusted writes; redacts e-mail addresses and phone numbers."""

    async def evaluate_write(self, item: MemoryItem) -> MemoryWriteDecision:
        if item.provenance.trust is Trust.UNTRUSTED and not item.provenance.validated:
            return MemoryWriteDecision(
                allowed=False, reasons=("untrusted content must be validated before storing",)
            )
        redacted = PHONE.sub("[PHONE]", EMAIL.sub("[EMAIL]", item.content))
        reasons = ("redacted personal data",) if redacted != item.content else ()
        return MemoryWriteDecision(
            allowed=True, item=item.model_copy(update={"content": redacted}), reasons=reasons
        )


class ReadOnlyMemoryPolicy(MemoryPolicy):
    """Memory can be read but never written by agents (the 'read-only memory' option)."""

    async def evaluate_write(self, item: MemoryItem) -> MemoryWriteDecision:
        return MemoryWriteDecision(allowed=False, reasons=("memory is read-only",))


def delimit(message: Message) -> Message:
    """Wrap untrusted content so the model sees it as data, never as instructions."""
    if message.trust is not Trust.UNTRUSTED:
        return message
    body = message.content.replace("</untrusted>", "&lt;/untrusted&gt;")
    source = message.name or message.role.value
    wrapped = f'<untrusted source="{source}">\n{body}\n</untrusted>'
    return message.model_copy(update={"content": wrapped})


class _Budgeted(ContextManager):
    def count_tokens(self, messages: Sequence[Message]) -> int:
        return sum(approx_tokens(m.content) + 4 for m in messages)

    def _summary(self, dropped: list[Message]) -> list[Message]:
        return []

    def build(self, sections: Sequence[ContextSection], budget_tokens: int) -> list[Message]:
        required = [s for s in sections if s.required]
        optional = sorted((s for s in sections if not s.required), key=lambda s: -s.priority)
        keep: set[int] = {id(m) for s in required for m in s.messages}
        used = self.count_tokens([delimit(m) for s in required for m in s.messages])
        if used > budget_tokens:
            raise ValueError("required sections alone exceed the context budget")
        dropped: list[Message] = []
        for section in optional:
            for m in reversed(section.messages):  # newest first within a section
                cost = self.count_tokens([delimit(m)])
                reserve = self.count_tokens(self._summary([*dropped, m]))
                if used + cost + reserve <= budget_tokens:
                    keep.add(id(m))
                    used += cost
                else:
                    dropped.append(m)
        ordered = [delimit(m) for s in sections for m in s.messages if id(m) in keep]
        system = [m for m in ordered if m.role is Role.SYSTEM]
        rest = [m for m in ordered if m.role is not Role.SYSTEM]
        return system + self._summary(dropped) + rest


class TrimContextManager(_Budgeted):
    """Drops the oldest, lowest-priority messages until the context fits."""


class SummaryStubContextManager(_Budgeted):
    """Replaces dropped messages with one summary placeholder (real summaries: Step 21)."""

    def _summary(self, dropped: list[Message]) -> list[Message]:
        if not dropped:
            return []
        return [Message(role=Role.SYSTEM, content=f"[summary of {len(dropped)} earlier messages]")]


mark_dummies(globals())
