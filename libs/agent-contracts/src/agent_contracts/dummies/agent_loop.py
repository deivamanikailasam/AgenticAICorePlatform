"""Test-double agent loops (Step 13): small, readable ReAct loops that pass AgentLoopContract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent_contracts.dummies._util import mark_dummies
from agent_contracts.interfaces import AgentLoop, ModelClient, StopRun, ToolExecutor, ToolRegistry
from agent_contracts.models import ChatRequest, Message, PlanNode, Role, RunContext, Trust


class MinimalReactLoop(AgentLoop):
    """Model → tools (one at a time) → model until an answer; unknown tools end the loop."""

    def __init__(
        self,
        model: ModelClient,
        tools: ToolExecutor,
        registry: ToolRegistry,
        names: Mapping[str, str] | None = None,
    ) -> None:
        self.model, self.tools, self.registry = model, tools, registry

    async def __call__(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> Any:
        cfg = dict(node.config.get("config") or {})
        defs = {
            name: (await self.registry.get(str(name))).spec.definition()
            for name in node.config.get("tools", ())
        }
        messages = [Message(role=Role.USER, content=json.dumps(values.get("input", {})))]
        limit = int(node.config.get("max_iterations") or 5)
        for step in range(1, limit + 1):
            reply = (
                await self.model.chat(
                    ChatRequest(
                        model=str(node.config.get("model") or "default"),
                        messages=tuple(messages),
                        tools=tuple(defs.values()),
                    )
                )
            ).message
            if not reply.tool_calls:
                answer: Any = reply.content
                if cfg.get("output_schema"):
                    answer = json.loads(answer)
                return {"answer": answer, "steps": step}
            messages.append(reply)
            for call in reply.tool_calls:
                if call.name not in defs:
                    raise ValueError(f"unknown tool '{call.name}'")
                result = await self.tools.execute(call, ctx)
                messages.append(
                    Message(
                        role=Role.TOOL,
                        tool_call_id=call.id,
                        content=json.dumps(result.output if result.ok else {"error": result.error}),
                        trust=Trust.UNTRUSTED,
                    )
                )
        raise StopRun(f"max_steps ({limit}) reached")


class SingleShotToolLoop(MinimalReactLoop):
    """Same loop with a step limit of at most three model turns (a cheap, impatient agent)."""

    async def __call__(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> Any:
        limit = min(int(node.config.get("max_iterations") or 3), 3)
        capped = node.model_copy(update={"config": {**node.config, "max_iterations": limit}})
        return await super().__call__(capped, values, ctx)


mark_dummies(globals())
