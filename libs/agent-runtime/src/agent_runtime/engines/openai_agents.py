"""Agent-loop engine on the OpenAI Agents SDK (Step 13, ADR 0022): ``components: {agent_loop: openai_agents}``.

The SDK runs the loop (turns, tool dispatch, final output); the platform keeps everything that
matters for governance:

* **models** — a bridge implements the SDK's ``Model`` on the platform model client, so calls go
  through the LiteLLM gateway by role, are costed, and never go to OpenAI directly;
* **tools** — each granted tool becomes an SDK ``FunctionTool`` whose body calls the platform
  tool executor (policy, rate limit, audit, idempotency key ``<run>:<node>:<n>``);
* **tracing to OpenAI is disabled** (``tracing_disabled=True``); the platform's own OTel tracing
  applies;
* tool output returns to the model as untrusted data; ``max_iterations`` is the SDK's
  ``max_turns`` (exceeding it stops the run); ``output_schema`` is enforced through a JSON-schema
  output type.

Tools that need approval are refused here: approvals pause and resume the plan, which only the
native loop supports (use ``agent_loop: native`` for such agents).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping
from typing import Any

import jsonschema
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
from agents.agent_output import AgentOutputSchemaBase
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from agent_runtime.agent_loop import DEFAULT_SYSTEM
from agent_runtime.executors import render
from agents import Agent, FunctionTool, RunConfig, Runner


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, Mapping))
    return str(content)


def to_messages(system: str | None, items: str | list[Any]) -> list[Message]:
    """SDK input items (Responses format) → platform messages."""
    messages = [Message(role=Role.SYSTEM, content=system)] if system else []
    if isinstance(items, str):
        return [*messages, Message(role=Role.USER, content=items)]
    pending_calls: list[ToolCall] = []

    def flush() -> None:
        if pending_calls:
            messages.append(Message(role=Role.ASSISTANT, tool_calls=tuple(pending_calls)))
            pending_calls.clear()

    for raw in items:
        item = raw if isinstance(raw, Mapping) else raw.model_dump()
        kind = item.get("type")
        if kind == "function_call":
            args = json.loads(item.get("arguments") or "{}")
            pending_calls.append(ToolCall(id=item["call_id"], name=item["name"], arguments=args))
            continue
        flush()
        if kind == "function_call_output":
            messages.append(
                Message(
                    role=Role.TOOL,
                    tool_call_id=item["call_id"],
                    content=_text(item.get("output", "")),
                    trust=Trust.UNTRUSTED,
                )
            )
        elif item.get("role") in ("user", "system", "developer", "assistant"):
            role = {"developer": Role.SYSTEM}.get(item["role"], Role(item["role"]))
            messages.append(Message(role=role, content=_text(item.get("content", ""))))
    flush()
    return messages


class BridgeModel(Model):
    """The SDK's ``Model`` on the platform's model client."""

    def __init__(self, client: ModelClient, model: str, metadata: Mapping[str, str]) -> None:
        self.client = client
        self.model = model
        self.metadata = dict(metadata)
        self.calls = 0

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[Any],
        model_settings: Any,
        tools: list[Any],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Any],
        tracing: Any,
        **_: Any,
    ) -> ModelResponse:
        self.calls += 1
        definitions = tuple(
            ToolDefinition(name=t.name, description=t.description, parameters=t.params_json_schema)
            for t in tools
            if isinstance(t, FunctionTool)
        )
        schema = (
            output_schema.json_schema()
            if output_schema is not None and not output_schema.is_plain_text()
            else None
        )
        response = await self.client.chat(
            ChatRequest(
                model=self.model,
                messages=tuple(to_messages(system_instructions, input)),
                tools=definitions,
                response_schema=schema if not definitions else None,
                metadata=self.metadata,
            )
        )
        reply = response.message
        output: list[Any] = [
            ResponseFunctionToolCall(
                type="function_call",
                call_id=c.id,
                name=c.name,
                arguments=json.dumps(c.arguments),
                id=f"fc_{c.id}",
                status="completed",
            )
            for c in reply.tool_calls
        ]
        if reply.content or not output:
            output.append(
                ResponseOutputMessage(
                    type="message",
                    id=f"msg_{self.calls}",
                    role="assistant",
                    status="completed",
                    content=[
                        ResponseOutputText(type="output_text", text=reply.content, annotations=[])
                    ],
                )
            )
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("streaming through the SDK bridge arrives with the agent API")


class _JsonSchemaOutput(AgentOutputSchemaBase):
    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema

    def is_plain_text(self) -> bool:
        return False

    def name(self) -> str:
        return "final_answer"

    def json_schema(self) -> dict[str, Any]:
        return self.schema

    def is_strict_json_schema(self) -> bool:
        return False

    def validate_json(self, json_str: str) -> Any:
        try:
            data = json.loads(json_str.strip().removeprefix("```json").removesuffix("```"))
            jsonschema.validate(data, self.schema)
        except (json.JSONDecodeError, jsonschema.ValidationError) as exc:
            raise ModelBehaviorError(
                f"final answer does not match the output schema: {exc}"
            ) from exc
        return data


class OpenAIAgentsLoopExecutor(AgentLoop):
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

    async def _function_tools(self, node: PlanNode, ctx: RunContext) -> list[FunctionTool]:
        counter = itertools.count()
        result = []
        for spec_name in node.config.get("tools", ()):
            name = self.names.get(str(spec_name), str(spec_name))
            spec = (await self.registry.get(name)).spec

            async def invoke(_ctx: Any, args: str, tool_name: str = name) -> str:
                call = ToolCall(
                    name=tool_name,
                    arguments=json.loads(args or "{}"),
                    idempotency_key=f"{ctx.run_id}:{node.id}:{next(counter)}",
                )
                out = await self.tools.execute(call, ctx)
                if out.ok:
                    return json.dumps(out.output, default=str)
                code = out.error_code.value if out.error_code else "failed"
                return json.dumps({"error": f"{code}: {out.error}"})

            result.append(
                FunctionTool(
                    name=name,
                    description=spec.description,
                    params_json_schema=spec.input_schema,
                    on_invoke_tool=invoke,
                    strict_json_schema=False,
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
            {"run_id": ctx.run_id, "node_id": node.id, "engine": "openai_agents"},
        )
        schema = cfg.get("output_schema")
        agent: Agent[Any] = Agent(
            name=node.id.replace(".", "_"),
            instructions=str(cfg.get("system") or DEFAULT_SYSTEM),
            tools=list(await self._function_tools(node, ctx)),
            model=bridge,
            output_type=_JsonSchemaOutput(schema) if schema else None,
        )
        task = (
            render(str(cfg["template"]), values)
            if cfg.get("template")
            else json.dumps(values.get("input", {}), default=str)
        )
        limit = int(node.config.get("max_iterations") or 10)
        try:
            result = await Runner.run(
                agent, task, max_turns=limit, run_config=RunConfig(tracing_disabled=True)
            )
        except MaxTurnsExceeded as exc:
            raise StopRun(f"max_steps ({limit}) reached", {"steps": bridge.calls}) from exc
        except ModelBehaviorError as exc:
            raise ValueError(f"the model misbehaved: {exc}") from exc
        return {"answer": result.final_output, "steps": bridge.calls}
