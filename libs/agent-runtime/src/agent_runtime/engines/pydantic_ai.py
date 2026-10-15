"""Agent-loop engine on Pydantic AI (Step 13, ADR 0022): ``components: {agent_loop: pydantic_ai}``.

Pydantic AI runs the loop; the platform keeps governance:

* **models** — a bridge implements Pydantic AI's ``Model`` on the platform model client (gateway
  by role, costed); no provider SDK or key is involved;
* **tools** — each granted tool is a Pydantic AI ``Tool`` built from its JSON schema whose body
  calls the platform tool executor (policy, rate limit, audit, idempotency ``<run>:<node>:<n>``);
* **no Logfire or other export** is configured; the platform's OTel tracing applies;
* tool output returns to the model as untrusted data; ``max_iterations`` is the request limit
  (exceeding it stops the run); ``output_schema`` uses native structured output.

Tools that need approval are refused (use the native loop for them).
"""

from __future__ import annotations

import itertools
import json
import os
from collections.abc import Mapping
from typing import Any

from agent_contracts.interfaces import AgentLoop, ModelClient, StopRun, ToolExecutor, ToolRegistry
from agent_contracts.models import (
    ChatRequest,
    Message,
    PlanNode,
    Role,
    RunContext,
    ToolCall,
    ToolDefinition,
    Trust,
)

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from pydantic_ai import Agent, Tool, UsageLimits
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.output import NativeOutput, StructuredDict

from agent_runtime.agent_loop import DEFAULT_SYSTEM
from agent_runtime.executors import render


def _content(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def to_messages(history: list[ModelMessage]) -> list[Message]:
    """Pydantic AI history → platform messages."""
    out: list[Message] = []
    for message in history:
        if isinstance(message, ModelResponse):
            calls = tuple(
                ToolCall(
                    id=p.tool_call_id,
                    name=p.tool_name,
                    arguments=p.args if isinstance(p.args, dict) else json.loads(p.args or "{}"),
                )
                for p in message.parts
                if isinstance(p, ToolCallPart)
            )
            text = "".join(p.content for p in message.parts if isinstance(p, TextPart))
            out.append(Message(role=Role.ASSISTANT, content=text, tool_calls=calls))
            continue
        for part in message.parts:
            if isinstance(part, SystemPromptPart):
                out.append(Message(role=Role.SYSTEM, content=part.content))
            elif isinstance(part, UserPromptPart):
                out.append(Message(role=Role.USER, content=_content(part.content)))
            elif isinstance(part, ToolReturnPart):
                out.append(
                    Message(
                        role=Role.TOOL,
                        name=part.tool_name,
                        tool_call_id=part.tool_call_id,
                        content=_content(part.content),
                        trust=Trust.UNTRUSTED,
                    )
                )
            elif isinstance(part, RetryPromptPart):
                text = part.model_response()
                if part.tool_name:
                    out.append(
                        Message(
                            role=Role.TOOL,
                            name=part.tool_name,
                            tool_call_id=part.tool_call_id,
                            content=json.dumps({"error": text}),
                        )
                    )
                else:
                    out.append(Message(role=Role.USER, content=text))
    return out


class BridgeModel(Model):
    """Pydantic AI's ``Model`` on the platform model client."""

    def __init__(self, client: ModelClient, model: str, metadata: Mapping[str, str]) -> None:
        super().__init__()
        self.client = client
        self.role = model
        self.metadata = dict(metadata)
        self.calls = 0

    @property
    def model_name(self) -> str:
        return self.role

    @property
    def system(self) -> str:
        return "agent-platform"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: Any,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self.calls += 1
        params = model_request_parameters
        tools = tuple(
            ToolDefinition(
                name=t.name, description=t.description or "", parameters=t.parameters_json_schema
            )
            for t in params.function_tools
        )
        schema = None
        if getattr(params, "output_mode", None) == "native" and params.output_object is not None:
            schema = dict(params.output_object.json_schema)
        response = await self.client.chat(
            ChatRequest(
                model=self.role,
                messages=tuple(to_messages(messages)),
                tools=tools,
                response_schema=schema if not tools else None,
                metadata=self.metadata,
            )
        )
        reply = response.message
        parts: list[Any] = [
            ToolCallPart(tool_name=c.name, args=dict(c.arguments), tool_call_id=c.id)
            for c in reply.tool_calls
        ]
        if reply.content or not parts:
            parts.append(TextPart(content=reply.content))
        return ModelResponse(parts=parts, model_name=self.role)


class PydanticAILoopExecutor(AgentLoop):
    def __init__(
        self,
        model: ModelClient,
        tools: ToolExecutor,
        registry: ToolRegistry,
        names: Mapping[str, str] | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.registry = registry
        self.names = dict(names or {})

    async def _tools(self, node: PlanNode, ctx: RunContext) -> list[Tool[Any]]:
        counter = itertools.count()
        result = []
        for spec_name in node.config.get("tools", ()):
            name = self.names.get(str(spec_name), str(spec_name))
            spec = (await self.registry.get(name)).spec

            async def invoke(_bound_tool: str = name, **arguments: Any) -> Any:
                call = ToolCall(
                    name=_bound_tool,
                    arguments=arguments,
                    idempotency_key=f"{ctx.run_id}:{node.id}:{next(counter)}",
                )
                out = await self.tools.execute(call, ctx)
                if out.ok:
                    return out.output
                code = out.error_code.value if out.error_code else "failed"
                return {"error": f"{code}: {out.error}"}

            result.append(
                Tool.from_schema(
                    invoke, name=name, description=spec.description, json_schema=spec.input_schema
                )
            )
        return result

    async def __call__(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> Any:
        if node.config.get("approval_tools"):
            raise ValueError(
                "tools that need approval require the native agent loop (agent_loop: native)"
            )
        cfg = dict(node.config.get("config") or {})
        bridge = BridgeModel(
            self.model,
            str(node.config.get("model") or "default"),
            {"run_id": ctx.run_id, "node_id": node.id, "engine": "pydantic_ai"},
        )
        schema = cfg.get("output_schema")
        output_type: Any = (
            NativeOutput(StructuredDict(schema, name="final_answer")) if schema else str
        )
        agent: Agent[None, Any] = Agent(
            bridge,
            system_prompt=str(cfg.get("system") or DEFAULT_SYSTEM),
            tools=await self._tools(node, ctx),
            output_type=output_type,
            retries=int(cfg.get("max_repairs", 2)),
        )
        task = (
            render(str(cfg["template"]), values)
            if cfg.get("template")
            else json.dumps(values.get("input", {}), default=str)
        )
        limit = int(node.config.get("max_iterations") or 10)
        try:
            result = await agent.run(task, usage_limits=UsageLimits(request_limit=limit))
        except UsageLimitExceeded as exc:
            raise StopRun(f"max_steps ({limit}) reached", {"steps": bridge.calls}) from exc
        except UnexpectedModelBehavior as exc:
            raise ValueError(f"the model misbehaved: {exc}") from exc
        return {"answer": result.output, "steps": bridge.calls}
