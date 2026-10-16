"""The obedient adversary (Step 35): a stand-in model that does whatever any text in its context
tells it to.

Red-team scenarios assume the worst model, not a well-behaved one: if an attacker's words reach
the model, through the user's message, a retrieved document, a tool result, a memory or another
agent's output, the model follows them. What a scenario then tests is whether the platform's
controls hold anyway (policy, approvals, allowlists, guardrails, budgets), which is what a
release depends on; how often a particular real model falls for an attack is a separate,
model-specific measurement (promptfoo and garak, against the real model).

Attack payloads carry their instructions in a form this model can act on, next to the attacker's
natural-language wording:

* ``[[do: <tool> {json arguments}]]`` — call that tool (by its spec name; the model's name for
  it is found among the tools it was offered, and a tool it wasn't offered is called anyway);
* ``[[answer: {json} ]]`` or ``[[answer: text]]`` — give this as the final answer;
* ``[[delegate: <worker> text]]`` — hand off to that worker (team patterns that route by
  ``handoff_to``).

Before obeying anything it does the benign part of the job: ``prelude`` tool calls (e.g. look the
invoice up), which is how the attacker's text in a tool result reaches it. Each instruction is
followed once. With nothing left to follow, the model gives ``fallback``
(default: a refusal, which fails any output schema, so a run never succeeds by accident).
A guardrail that removes or quarantines the payload before it reaches the model is a defence
that worked: this model never sees the instruction.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

from agent_contracts.interfaces import ModelClient
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Message,
    ModelCapabilities,
    Role,
    ToolCall,
    Usage,
)

DIRECTIVE = re.compile(r"\[\[(do|answer|delegate):\s*(.*?)\]\]", re.DOTALL)
FALLBACK = "I can't help with that."


def directives(text: str) -> list[tuple[str, str]]:
    """Instructions in ``text``. A payload inside a JSON-encoded tool result arrives with its
    quotes escaped; it is read the way a model would read it, unescaped."""
    out = []
    for kind, body in DIRECTIVE.findall(text):
        body = body.strip()
        if '\\"' in body:
            with contextlib.suppress(json.JSONDecodeError):
                body = json.loads(f'"{body}"')
        out.append((kind, body))
    return out


class ObedientModel(ModelClient):
    def __init__(
        self,
        fallback: Any = FALLBACK,
        agent: str = "",
        prelude: Sequence[tuple[str, dict[str, Any]]] = (),
    ) -> None:
        self.fallback = fallback
        self.agent = agent
        self.prelude = list(prelude)
        self.done: set[str] = set()
        self.requests: list[ChatRequest] = []

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(tool_calling=True, structured_output=True)

    @staticmethod
    def _tool_name(name: str, request: ChatRequest) -> str:
        for d in request.tools:
            if d.name == name or d.name.endswith(f"__{name}"):
                return d.name
        return name

    def _reply(self, request: ChatRequest) -> Message:
        if self.prelude:
            name, args = self.prelude.pop(0)
            call = ToolCall(name=self._tool_name(name, request), arguments=dict(args))
            return Message(role=Role.ASSISTANT, tool_calls=(call,))
        seen = "\n".join(m.content for m in request.messages if m.role is not Role.SYSTEM)
        for kind, body in directives(seen):
            key = f"{kind}:{body}"
            if key in self.done:
                continue
            self.done.add(key)
            if kind == "do":
                name, _, raw = body.partition(" ")
                try:
                    args = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    args = {"raw": raw}
                call = ToolCall(name=self._tool_name(name, request), arguments=args)
                return Message(role=Role.ASSISTANT, tool_calls=(call,))
            if kind == "delegate":
                worker, _, note = body.partition(" ")
                content = json.dumps({"handoff_to": worker, "note": note})
                return Message(role=Role.ASSISTANT, content=content)
            return Message(role=Role.ASSISTANT, content=body)
        fallback = self.fallback
        content = fallback if isinstance(fallback, str) else json.dumps(fallback)
        return Message(role=Role.ASSISTANT, content=content)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        message = self._reply(request)
        return ChatResponse(
            message=message,
            model=request.model,
            usage=Usage(input_tokens=40, output_tokens=10),
            finish_reason="tool_calls" if message.tool_calls else "stop",
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        response = await self.chat(request)
        yield ChatChunk(
            delta=response.message.content,
            tool_calls=response.message.tool_calls,
            finish_reason=response.finish_reason,
        )

    def saw(self, needle: str) -> bool:
        """Whether any request put ``needle`` in front of the model (did the payload get
        through?)."""
        return any(needle in m.content for r in self.requests for m in r.messages)


__all__ = ["DIRECTIVE", "FALLBACK", "ObedientModel", "directives"]
