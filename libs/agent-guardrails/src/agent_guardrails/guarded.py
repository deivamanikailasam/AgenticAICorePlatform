"""Guardrails around every model and tool call of a run (Step 27).

Model calls:
* the user's messages go through ``input`` checks; a block stops the run
  (``StopRun("guardrail: input blocked …")``);
* untrusted messages (tool results, retrieved documents, memory, other agents' output) go
  through ``retrieved`` checks; a flagged message is *quarantined* — its content is replaced by a
  notice, so the model never sees the injected text — and the run continues;
* the answer goes through ``output`` checks: redactions are applied, a block stops the run.

Tool calls: arguments through ``tool_args`` (a block refuses the call with ``denied``), results
through ``tool_result`` (injected results are quarantined, secrets and PII redacted).

Verdicts on untrusted text are cached by content, so a tool result checked when it came back is
not checked again each time the conversation is sent to the model.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from agent_contracts.interfaces import ModelClient, StopRun, ToolExecutor
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    GuardrailStage,
    Message,
    ModelCapabilities,
    Role,
    RunContext,
    ToolCall,
    ToolErrorCode,
    ToolResult,
    ToolSpec,
    Trust,
)

from agent_guardrails.pipeline import GuardrailPipeline, Outcome

QUARANTINE = "[content removed by guardrails: {reason}]"
ContextOf = Callable[[str | None], RunContext | None]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class GuardrailRuntime:
    """What a run's guarded model client and tool executor share: the pipeline, a cache of
    verdicts on untrusted text, and how to find a run's context (for audit)."""

    def __init__(self, pipeline: GuardrailPipeline, context_of: ContextOf | None = None) -> None:
        self.pipeline = pipeline
        self.context_of = context_of or (lambda run_id: None)
        self._untrusted: dict[str, Outcome] = {}

    async def untrusted(
        self, stage: GuardrailStage, text: str, ctx: RunContext | None, tool: str | None = None
    ) -> Outcome:
        key = _digest(text)
        if key not in self._untrusted:
            if len(self._untrusted) > 4096:
                self._untrusted.clear()
            self._untrusted[key] = await self.pipeline.run(
                stage, text, trust=Trust.UNTRUSTED, ctx=ctx, tool=tool
            )
        return self._untrusted[key]

    async def screen_tool(self, spec: ToolSpec, ctx: RunContext | None = None) -> str | None:
        """Why a tool's description may not be shown to a model (an injection in its description
        or parameter descriptions), or None."""
        text = f"{spec.name}: {spec.description}\n{json.dumps(spec.input_schema, sort_keys=True)}"
        outcome = await self.untrusted(GuardrailStage.RETRIEVED, text, ctx, tool=spec.name)
        return outcome.reason if outcome.blocked else None


class GuardedModelClient(ModelClient):
    def __init__(self, inner: ModelClient, runtime: GuardrailRuntime) -> None:
        self.inner = inner
        self.rt = runtime

    def capabilities(self, model: str) -> ModelCapabilities:
        return self.inner.capabilities(model)

    async def _inputs(self, request: ChatRequest) -> ChatRequest:
        ctx = self.rt.context_of((request.metadata or {}).get("run_id"))
        messages: list[Message] = []
        last_user = max(
            (
                i
                for i, m in enumerate(request.messages)
                if m.role is Role.USER and m.trust is not Trust.UNTRUSTED
            ),
            default=-1,
        )
        for i, m in enumerate(request.messages):
            if m.role in (Role.SYSTEM, Role.ASSISTANT) or not m.content:
                messages.append(m)
            elif m.role is Role.USER and m.trust is not Trust.UNTRUSTED:  # the person's request
                if i == last_user:  # earlier turns were checked when they were new
                    outcome = await self.rt.pipeline.run(
                        GuardrailStage.INPUT, m.content, trust=Trust.USER, ctx=ctx
                    )
                    if outcome.blocked:
                        raise StopRun(
                            f"guardrail: input blocked ({outcome.reason})",
                            {"guardrail": outcome.findings},
                        )
                    m = m.model_copy(update={"content": outcome.text})
                messages.append(m)
            else:
                outcome = await self.rt.untrusted(GuardrailStage.RETRIEVED, m.content, ctx, m.name)
                if outcome.blocked:
                    m = m.model_copy(update={"content": QUARANTINE.format(reason=outcome.reason)})
                messages.append(m)
        return request.model_copy(update={"messages": tuple(messages)})

    async def _output(self, request: ChatRequest, response: ChatResponse) -> ChatResponse:
        text = response.message.content
        if not text:
            return response
        ctx = self.rt.context_of((request.metadata or {}).get("run_id"))
        outcome = await self.rt.pipeline.run(
            GuardrailStage.OUTPUT, text, data={"output": text}, ctx=ctx
        )
        if outcome.blocked:
            raise StopRun(
                f"guardrail: output blocked ({outcome.reason})", {"guardrail": outcome.findings}
            )
        if outcome.text != text:
            return response.model_copy(
                update={"message": response.message.model_copy(update={"content": outcome.text})}
            )
        return response

    async def chat(self, request: ChatRequest) -> ChatResponse:
        guarded = await self._inputs(request)
        return await self._output(guarded, await self.inner.chat(guarded))

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        """Output checks need the whole answer: the stream is collected, checked, then sent as
        one chunk (inputs are checked before the call as usual)."""
        guarded = await self._inputs(request)
        text, last = "", None
        async for chunk in self.inner.stream(guarded):
            text += chunk.delta
            last = chunk
        response = ChatResponse(
            message=Message(
                role=Role.ASSISTANT, content=text, tool_calls=last.tool_calls if last else ()
            ),
            model=guarded.model,
        )
        checked = await self._output(guarded, response)
        yield ChatChunk(
            delta=checked.message.content,
            tool_calls=checked.message.tool_calls,
            finish_reason=last.finish_reason if last else "stop",
            usage=last.usage if last else None,
        )


class GuardedToolExecutor(ToolExecutor):
    def __init__(
        self,
        inner: ToolExecutor,
        runtime: GuardrailRuntime,
        schema_of: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None,
    ) -> None:
        self.inner = inner
        self.rt = runtime
        self.registry = getattr(inner, "registry", None)
        self.schema_of = schema_of

    async def _schema(self, name: str) -> dict[str, Any] | None:
        if self.schema_of is not None:
            return await self.schema_of(name)
        if self.registry is not None:
            try:
                return dict((await self.registry.get(name)).spec.input_schema)
            except KeyError:
                return None
        return None

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        args = await self.rt.pipeline.run(
            GuardrailStage.TOOL_ARGS,
            json.dumps(call.arguments, sort_keys=True, default=str),
            data={"arguments": call.arguments, "schema": await self._schema(call.name)},
            tool=call.name,
            ctx=ctx,
        )
        if args.blocked:
            return ToolResult(
                call_id=call.id,
                tool=call.name,
                ok=False,
                error_code=ToolErrorCode.DENIED,
                error=f"guardrail: {args.reason}",
            )
        result = await self.inner.execute(call, ctx)
        if not result.ok or result.output is None:
            return result
        text = (
            result.output
            if isinstance(result.output, str)
            else json.dumps(result.output, default=str)
        )
        outcome = await self.rt.untrusted(GuardrailStage.TOOL_RESULT, text, ctx, call.name)
        if outcome.blocked:
            return result.model_copy(
                update={
                    "output": {
                        "removed_by_guardrails": outcome.findings,
                        "note": QUARANTINE.format(reason=outcome.reason),
                    }
                }
            )
        if outcome.text != text:
            try:
                redacted: Any = json.loads(outcome.text)
            except json.JSONDecodeError:
                redacted = outcome.text
            return result.model_copy(update={"output": redacted})
        return result
