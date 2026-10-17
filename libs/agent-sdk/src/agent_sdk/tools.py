"""Tools from plain async functions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent_contracts.interfaces import Tool
from agent_contracts.models import DataClass, RunContext, SideEffect, ToolSpec

ToolFunction = Callable[[Mapping[str, Any], RunContext], Awaitable[Any]]


def tool(
    *,
    name: str,
    ref: str,
    description: str,
    input_schema: Mapping[str, Any],
    side_effect: SideEffect = SideEffect.READ,
    data_classification: DataClass = DataClass.INTERNAL,
    **spec: Any,
) -> Callable[[ToolFunction], type[Tool]]:
    """Make a ``Tool`` class from an async function ``(arguments, ctx) -> result``.

    Register the class in your package's ``pyproject.toml``::

        [project.entry-points."agent_platform.components"]
        "tool.team-a.shout" = "team_a.tools:shout"

    and grant it in ``agent.yaml`` with ``tools: [{ref: local://team-a/shout}]``. The run API loads
    it only by that exact ``ref``. Arguments arrive validated against ``input_schema``. Every call
    passes policy (OPA), guardrails and budgets, and is audited; a ``side_effect`` of
    ``irreversible`` always waits for a person's approval. Other ``ToolSpec`` fields
    (``output_schema``, ``idempotent``, ``timeout_seconds``, ``owner``, ``tags`` ...) go in
    ``spec``.
    """
    if not ref.startswith("local://"):
        raise ValueError(f"an SDK tool's ref is local://<project>/<tool>, not {ref!r}")
    tool_spec = ToolSpec(
        name=name,
        ref=ref,
        description=description,
        input_schema=dict(input_schema),
        side_effect=side_effect,
        data_classification=data_classification,
        **spec,
    )

    def wrap(fn: ToolFunction) -> type[Tool]:
        async def invoke(self: Tool, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
            return await fn(arguments, ctx)

        cls = type(
            fn.__name__,
            (Tool,),
            {
                "spec": property(lambda self: tool_spec),
                "invoke": invoke,
                "__doc__": fn.__doc__ or description,
                "__module__": fn.__module__,
                "__qualname__": fn.__qualname__,
                "function": staticmethod(fn),
            },
        )
        return cls

    return wrap
