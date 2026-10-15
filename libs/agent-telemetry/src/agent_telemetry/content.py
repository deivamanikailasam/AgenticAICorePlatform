"""Prompt and response content on spans: off unless Langfuse is configured, always redacted.

``setup()`` turns capture on only when a Langfuse exporter exists; every other exporter gets
spans through ``ContentFreeExporter``, which drops the content attributes. Before content is put
on a span it passes the redactor (secret shapes always; PII with Presidio when configured). If
redaction is unavailable, the content is withheld, never recorded raw.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from agent_contracts.models import Message, Role, ToolCall

#: async text → redacted text; raises (or returns None) when it cannot redact
Redactor = Callable[[str], Awaitable[str | None]]
WITHHELD = "[content withheld: redaction unavailable]"


class ContentPolicy:
    """Whether to capture content and how to redact it. One per process (``setup``)."""

    def __init__(self, capture: bool = False, redactor: Redactor | None = None) -> None:
        self.capture = capture
        self.redactor = redactor

    async def redact(self, text: str) -> str:
        if self.redactor is None:
            return WITHHELD  # capture without a redactor is refused
        try:
            redacted = await self.redactor(text)
        except Exception:
            return WITHHELD
        return WITHHELD if redacted is None else redacted


policy = ContentPolicy()


def configure(capture: bool, redactor: Redactor | None) -> None:
    policy.capture, policy.redactor = capture, redactor


def _parts(message: Message) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if message.role is Role.TOOL:
        return [
            {"type": "tool_call_response", "id": message.tool_call_id, "response": message.content}
        ]
    if message.content:
        parts.append({"type": "text", "content": message.content})
    parts += [_call(c) for c in message.tool_calls]
    return parts


def _call(call: ToolCall) -> dict[str, Any]:
    return {"type": "tool_call", "id": call.id, "name": call.name, "arguments": call.arguments}


def messages_json(messages: Sequence[Message]) -> tuple[str, str]:
    """(system instructions, chat history) in the conventions' message format, as JSON."""
    system = [{"type": "text", "content": m.content} for m in messages if m.role is Role.SYSTEM]
    history = [
        {"role": m.role.value, "parts": _parts(m)} for m in messages if m.role is not Role.SYSTEM
    ]
    return json.dumps(system, default=str), json.dumps(history, default=str)


def output_json(message: Message, finish_reason: str) -> str:
    return json.dumps(
        [{"role": "assistant", "parts": _parts(message), "finish_reason": finish_reason}],
        default=str,
    )
