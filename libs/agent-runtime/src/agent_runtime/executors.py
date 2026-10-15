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
  ``config.gate`` validates the output before the next step (Step 15, ``gates.py``);
* **router** — picks one of ``routes``: with ``fn`` by a function, otherwise by a ``Router``
  component (LLM with a schema that only allows the declared routes, embedding classifier or
  rules; Step 15), with an optional confidence floor and fallback route;
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

from agent_contracts.interfaces import ModelClient, Router, ToolExecutor
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
    """One model call; with ``config.gate`` the output must pass the gate (``gates.py``) before
    the run moves on: failures are sent back with the reasons, at most ``max_retries`` times."""

    def __init__(
        self,
        model: ModelClient,
        functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        self.model = model
        self.functions = dict(functions or {})

    async def _chat(
        self,
        node: PlanNode,
        prompt: str | list[Message],
        ctx: RunContext,
        schema: dict[str, Any] | None,
    ) -> Any:
        return self._parse(await self._reply(node, prompt, ctx, schema), schema)

    async def _reply(
        self,
        node: PlanNode,
        prompt: str | list[Message],
        ctx: RunContext,
        schema: dict[str, Any] | None,
    ) -> str:
        cfg = _cfg(node)
        messages = []
        if cfg.get("system"):
            messages.append(Message(role=Role.SYSTEM, content=str(cfg["system"])))
        if isinstance(prompt, str):
            messages.append(Message(role=Role.USER, content=prompt))
        else:
            messages += prompt
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
        return response.message.content or ""

    @staticmethod
    def _parse(text: str, schema: dict[str, Any] | None) -> Any:
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
        prompt = render(str(cfg["template"]), values)
        schema = cfg.get("output_schema")
        gate = cfg.get("gate")
        if not gate:
            return await self._chat(node, prompt, ctx, schema)
        from agent_runtime.gates import GateFailedError, gate_reasons

        conversation = [Message(role=Role.USER, content=prompt)]
        reasons: list[str] = []
        for _ in range(int(gate.get("max_retries", 1)) + 1):
            text = await self._reply(node, conversation, ctx, schema)
            try:
                output = self._parse(text, schema)
            except json.JSONDecodeError as exc:
                reasons = [f"not valid JSON: {exc.msg}"]
            else:
                reasons = gate_reasons(gate, output, values, self.functions)
                if not reasons:
                    return output
            conversation += [
                Message(role=Role.ASSISTANT, content=text),
                Message(
                    role=Role.USER,
                    content="Your answer failed validation: "
                    + "; ".join(reasons)
                    + ". Answer the original request again, fixing these problems.",
                ),
            ]
        raise GateFailedError(node.id, reasons)


RouterFactory = Callable[[PlanNode], Router]


class RouterNodeExecutor:
    """Returns ``{"_outcome": <route>, ...}``; only a declared route can come back.

    With ``fn`` a function decides; otherwise a ``Router`` component from ``routers(node)`` (the
    composition root picks llm, embedding or rules from ``config.router`` or ``components``), or
    the LLM router on the node's model. ``config.min_confidence`` with ``config.fallback``
    sends unsure decisions down the fallback route (for example to a human or a clarifying step).
    """

    def __init__(
        self,
        model: ModelClient | None = None,
        functions: Mapping[str, Callable[[dict[str, Any]], Any]] | None = None,
        routers: RouterFactory | None = None,
    ) -> None:
        self.model = model
        self.functions = dict(functions or {})
        self.routers = routers

    def _router(self, node: PlanNode) -> Router:
        if self.routers is not None:
            return self.routers(node)
        if self.model is None:
            raise ValueError(f"router '{node.id}' needs fn, a router or a model client")
        from agent_runtime.routing import LlmRouter

        cfg = _cfg(node)
        return LlmRouter(
            self.model, str(node.config.get("model") or "router"), cfg.get("descriptions")
        )

    async def __call__(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> Any:
        routes = [str(r) for r in node.config.get("routes", ())]
        cfg = _cfg(node)
        if node.config.get("fn"):
            route = self.functions[str(node.config["fn"])](values)
            extra: dict[str, Any] = {}
        else:
            text = render(str(cfg.get("template") or "$input"), values)
            decision = await self._router(node).route(text, routes, ctx)
            route = decision.route
            extra = {"confidence": decision.confidence, "reason": decision.reason}
            floor = cfg.get("min_confidence")
            if floor is not None and decision.confidence < float(floor) and cfg.get("fallback"):
                extra["chosen"] = route
                route = str(cfg["fallback"])
        if route not in routes:
            raise ValueError(f"router '{node.id}' chose '{route}', not one of {routes}")
        return {"_outcome": route, "route": route, **extra}


class ToolNodeError(RuntimeError):
    pass


class ToolNodeExecutor:
    """Calls the node's tool through the (authorising) tool executor.

    ``names`` maps a spec tool name (``post_payment``) to the registry's model-facing name
    (``erp__post_payment``); unmapped names are used as they are. Behind an approval node the
    decision travels with the call as ``ApprovalEvidence`` bound to these exact arguments.
    """

    def __init__(self, tools: ToolExecutor, names: Mapping[str, str] | None = None) -> None:
        self.tools = tools
        self.names = dict(names or {})

    async def __call__(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> Any:
        from agent_runtime.approvals import ApproverNotAllowedError, evidence_for

        spec_name = str(node.config["tool"])
        name = self.names.get(spec_name, spec_name)
        cfg = _cfg(node)
        arguments = resolve(cfg.get("args", {}), values)
        gate = node.config.get("approval_node")
        try:
            evidence = (
                evidence_for(
                    values.get(str(gate)),
                    approval_id=f"{ctx.run_id}:{gate}",
                    tool=name,
                    arguments=arguments,
                    required_group=node.config.get("approvers"),
                    ctx=ctx,
                )
                if gate
                else None
            )
        except ApproverNotAllowedError as exc:
            raise ToolNodeError(f"approval_invalid: {exc}") from exc
        result = await self.tools.execute(
            ToolCall(
                name=name,
                arguments=arguments,
                idempotency_key=f"{ctx.run_id}:{node.id}",
                approval=evidence,
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
