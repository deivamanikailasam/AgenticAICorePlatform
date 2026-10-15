"""Executors for the non-function workflow nodes (Step 12): LLM, router, tool and sub-workflow.

They plug into ``NodeRunner(executors={NodeKind.LLM: ..., ...})`` and so work on every runtime
adapter. Node configuration comes from the spec (``model``, ``tool``, ``routes``, ``spec`` and the
free-form ``config``):

* **values in arguments and prompts** — ``"$input.invoice_id"`` or ``"${validate.total}"`` read
  the run's values by dotted path; nothing is evaluated, so a value can never become code.
  Model and tool outputs are untrusted data: they are inserted as text or JSON, never as
  instructions to the platform;
* **LLM** — ``config.template`` (and optional ``config.system``) with ``config.output_schema``
  for structured output, through the platform model client (a role resolves to a gateway alias);
* **router** — picks one of ``routes``: with ``fn`` by a function, otherwise by the model with a
  schema that only allows the declared routes;
* **tool** — through the authorising executor (policy, rate limit, audit, idempotency). The
  idempotency key is ``<run id>:<node id>``, so a retry, resume or replay of the same run can
  never repeat a side effect; ``config.args`` maps arguments to values;
* **sub-workflow** — compiles and runs another spec as a child run (``parent_run_id`` set).
"""

from __future__ import annotations

import json
import re
import string
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent_contracts.interfaces import ModelClient, ToolExecutor
from agent_contracts.models import (
    ChatRequest,
    Message,
    PlanNode,
    Role,
    RunContext,
    RunResult,
    RunStatus,
    ToolCall,
)

_MISSING = object()


def lookup(values: Mapping[str, Any], path: str) -> Any:
    """``a.b.0.c`` through dicts and lists; raises ``KeyError`` naming the path if absent."""
    current: Any = values
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise KeyError(f"no value at '{path}'")
    return current


def resolve(value: Any, values: Mapping[str, Any]) -> Any:
    """Replace ``"$path"`` strings (recursively in dicts and lists) with run values."""
    if isinstance(value, str) and value.startswith("$"):
        if value.startswith("$$"):
            return value[1:]
        return lookup(values, value[1:].strip("{}"))
    if isinstance(value, Mapping):
        return {k: resolve(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, values) for v in value]
    return value


class _DottedTemplate(string.Template):
    idpattern = r"(?a:[_a-z][_a-z0-9]*(?:\.[_a-z0-9]+)*)"
    flags = re.IGNORECASE


def render(template: str, values: Mapping[str, Any]) -> str:
    """``$name`` / ``${a.b}`` substitution only; values are inserted as text (dicts as JSON)."""

    class _View(dict[str, str]):
        def __missing__(self, key: str) -> str:
            found = lookup(values, key)
            return found if isinstance(found, str) else json.dumps(found, default=str)

    return _DottedTemplate(template).substitute(_View())


def _cfg(node: PlanNode) -> dict[str, Any]:
    inner = node.config.get("config") or {}
    return dict(inner) if isinstance(inner, Mapping) else {}


class LlmNodeExecutor:
    def __init__(self, model: ModelClient) -> None:
        self.model = model

    async def _chat(
        self, node: PlanNode, prompt: str, ctx: RunContext, schema: dict[str, Any] | None
    ) -> Any:
        cfg = _cfg(node)
        messages = []
        if cfg.get("system"):
            messages.append(Message(role=Role.SYSTEM, content=str(cfg["system"])))
        messages.append(Message(role=Role.USER, content=prompt))
        response = await self.model.chat(
            ChatRequest(
                model=str(node.config.get("model") or "default"),
                messages=tuple(messages),
                response_schema=schema,
                temperature=cfg.get("temperature"),
                max_tokens=cfg.get("max_tokens"),
                metadata={"run_id": ctx.run_id, "node_id": node.id, "tenant_id": ctx.tenant_id},
            )
        )
        text = response.message.content or ""
        return json.loads(text) if schema is not None else text

    async def __call__(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> Any:
        cfg = _cfg(node)
        if node.config.get("prompt") and not cfg.get("template"):
            raise NotImplementedError(
                f"prompt registry refs ({node.config['prompt']}) are resolved from Step 24; "
                "use config.template until then"
            )
        if not cfg.get("template"):
            raise ValueError(f"llm node '{node.id}' needs config.template")
        return await self._chat(
            node, render(str(cfg["template"]), values), ctx, cfg.get("output_schema")
        )


class RouterNodeExecutor:
    """Returns ``{"_outcome": <route>}``; only a declared route can come back."""

    def __init__(
        self,
        model: ModelClient | None = None,
        functions: Mapping[str, Callable[[dict[str, Any]], Any]] | None = None,
    ) -> None:
        self.llm = LlmNodeExecutor(model) if model is not None else None
        self.functions = dict(functions or {})

    async def __call__(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> Any:
        routes = [str(r) for r in node.config.get("routes", ())]
        if node.config.get("fn"):
            route = self.functions[str(node.config["fn"])](values)
        else:
            if self.llm is None:
                raise ValueError(f"router '{node.id}' needs fn or a model client")
            cfg = _cfg(node)
            template = str(cfg.get("template") or "Choose one route for: $input")
            answer = await self.llm._chat(
                node,
                render(template, values) + f"\n\nRoutes: {', '.join(routes)}",
                ctx,
                {
                    "type": "object",
                    "properties": {"route": {"type": "string", "enum": routes}},
                    "required": ["route"],
                    "additionalProperties": False,
                },
            )
            route = answer.get("route") if isinstance(answer, Mapping) else None
        if route not in routes:
            raise ValueError(f"router '{node.id}' chose '{route}', not one of {routes}")
        return {"_outcome": route, "route": route}


class ToolNodeError(RuntimeError):
    pass


class ToolNodeExecutor:
    """Calls the node's tool through the (authorising) tool executor.

    ``names`` maps a spec tool name (``post_payment``) to the registry's model-facing name
    (``erp__post_payment``); unmapped names are used as they are.
    """

    def __init__(self, tools: ToolExecutor, names: Mapping[str, str] | None = None) -> None:
        self.tools = tools
        self.names = dict(names or {})

    async def __call__(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> Any:
        spec_name = str(node.config["tool"])
        cfg = _cfg(node)
        arguments = resolve(cfg.get("args", {}), values)
        result = await self.tools.execute(
            ToolCall(
                name=self.names.get(spec_name, spec_name),
                arguments=arguments,
                idempotency_key=f"{ctx.run_id}:{node.id}",
            ),
            ctx,
        )
        if not result.ok:
            code = result.error_code.value if result.error_code else "failed"
            raise ToolNodeError(f"{code}: {result.error}")
        return result.output


RunSubplan = Callable[[str, dict[str, Any], RunContext], Awaitable[RunResult]]


class SubplanNodeExecutor:
    """Runs ``spec`` as a child run. ``run_subplan`` is provided by the composition root (the
    compiler package), which knows how to load and compile specs."""

    def __init__(self, run_subplan: RunSubplan) -> None:
        self.run_subplan = run_subplan

    async def __call__(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> Any:
        cfg = _cfg(node)
        inputs = resolve(cfg.get("inputs"), values) if "inputs" in cfg else values.get("input", {})
        child = ctx.model_copy(
            update={"run_id": f"{ctx.run_id}.{node.id}", "parent_run_id": ctx.run_id}
        )
        result = await self.run_subplan(str(node.config["spec"]), dict(inputs), child)
        if result.status is not RunStatus.SUCCEEDED:
            raise RuntimeError(
                f"sub-workflow {node.config['spec']} ended {result.status.value}: {result.state.error}"
            )
        return result.output
