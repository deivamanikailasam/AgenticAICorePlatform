from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from typing import Any

import jsonschema

from agent_contracts.interfaces import Tool, ToolExecutor, ToolRegistry
from agent_contracts.models import (
    RunContext,
    ToolCall,
    ToolErrorCode,
    ToolResult,
    ToolSpec,
)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) if p.isdigit() else 0 for p in version.split("."))


class EchoTool(Tool):
    """Returns its arguments unchanged."""

    def __init__(self, name: str = "echo", version: str = "1.0.0") -> None:
        self._spec = ToolSpec(
            name=name,
            version=version,
            description="Return the given text.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        return dict(arguments)


class AddTool(Tool):
    """Adds two numbers."""

    def __init__(self, name: str = "add", version: str = "1.0.0") -> None:
        self._spec = ToolSpec(
            name=name,
            version=version,
            description="Add a and b.",
            input_schema={
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
                "additionalProperties": False,
            },
            output_schema={"type": "number"},
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        return arguments["a"] + arguments["b"]


class InMemoryToolRegistry(ToolRegistry):
    """Dictionary keyed by (name, version)."""

    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], Tool] = {}
        self._deprecated: set[tuple[str, str]] = set()

    async def register(self, tool: Tool) -> None:
        key = (tool.spec.name, tool.spec.version)
        if key in self._tools:
            raise ValueError(f"tool {key[0]}@{key[1]} already registered")
        self._tools[key] = tool

    async def get(self, name: str, version: str | None = None) -> Tool:
        if version is not None:
            return self._tools[(name, version)]
        live = [k for k in self._tools if k[0] == name and k not in self._deprecated]
        if not live:
            raise KeyError(name)
        return self._tools[max(live, key=lambda k: _version_key(k[1]))]

    async def list(self, include_deprecated: bool = False) -> Sequence[ToolSpec]:
        return [
            t.spec.model_copy(update={"deprecated": k in self._deprecated})
            for k, t in sorted(self._tools.items())
            if include_deprecated or k not in self._deprecated
        ]

    async def deprecate(self, name: str, version: str) -> None:
        if (name, version) not in self._tools:
            raise KeyError(f"{name}@{version}")
        self._deprecated.add((name, version))


class IndexedToolRegistry(ToolRegistry):
    """Keeps a per-name version index, sorted newest first."""

    def __init__(self) -> None:
        self._index: dict[str, list[tuple[str, Tool, bool]]] = {}

    async def register(self, tool: Tool) -> None:
        versions = self._index.setdefault(tool.spec.name, [])
        if any(v == tool.spec.version for v, _, _ in versions):
            raise ValueError(f"tool {tool.spec.name}@{tool.spec.version} already registered")
        versions.append((tool.spec.version, tool, False))
        versions.sort(key=lambda e: _version_key(e[0]), reverse=True)

    async def get(self, name: str, version: str | None = None) -> Tool:
        for v, tool, deprecated in self._index.get(name, []):
            if (version is None and not deprecated) or v == version:
                return tool
        raise KeyError(name if version is None else f"{name}@{version}")

    async def list(self, include_deprecated: bool = False) -> Sequence[ToolSpec]:
        out = []
        for name in sorted(self._index):
            for _, tool, dep in sorted(self._index[name], key=lambda e: _version_key(e[0])):
                if include_deprecated or not dep:
                    out.append(tool.spec.model_copy(update={"deprecated": dep}))
        return out

    async def deprecate(self, name: str, version: str) -> None:
        entries = self._index.get(name, [])
        for i, (v, tool, _) in enumerate(entries):
            if v == version:
                entries[i] = (v, tool, True)
                return
        raise KeyError(f"{name}@{version}")


class LocalToolExecutor(ToolExecutor):
    """Validates arguments, enforces timeouts and replays results for repeated idempotency keys."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._results: dict[tuple[str, str, str], ToolResult] = {}

    async def _attempt(self, tool: Tool, call: ToolCall, ctx: RunContext) -> ToolResult:
        start = time.perf_counter()
        try:
            output = await asyncio.wait_for(
                tool.invoke(call.arguments, ctx), tool.spec.timeout_seconds
            )
        except TimeoutError:
            return self._fail(call, ToolErrorCode.TIMEOUT, "tool timed out", start)
        except Exception as exc:
            return self._fail(call, ToolErrorCode.FAILED, f"{type(exc).__name__}: {exc}", start)
        return ToolResult(
            call_id=call.id,
            tool=call.name,
            ok=True,
            output=output,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )

    @staticmethod
    def _fail(call: ToolCall, code: ToolErrorCode, msg: str, start: float) -> ToolResult:
        return ToolResult(
            call_id=call.id,
            tool=call.name,
            ok=False,
            error_code=code,
            error=msg,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        start = time.perf_counter()
        key = (ctx.tenant_id, call.name, call.idempotency_key) if call.idempotency_key else None
        if key and key in self._results:
            return self._results[key]
        try:
            tool = await self._registry.get(call.name)
        except KeyError:
            return self._fail(call, ToolErrorCode.NOT_FOUND, f"unknown tool {call.name}", start)
        try:
            jsonschema.validate(call.arguments, tool.spec.input_schema)
        except jsonschema.ValidationError as exc:
            return self._fail(call, ToolErrorCode.INVALID_ARGUMENTS, exc.message, start)
        result = await self._run(tool, call, ctx)
        if key and result.ok:
            self._results[key] = result
        return result

    async def _run(self, tool: Tool, call: ToolCall, ctx: RunContext) -> ToolResult:
        return await self._attempt(tool, call, ctx)


class RetryingToolExecutor(LocalToolExecutor):
    """Like ``local`` but honours each tool's retry policy for idempotent tools."""

    async def _run(self, tool: Tool, call: ToolCall, ctx: RunContext) -> ToolResult:
        attempts = tool.spec.retry.max_attempts if tool.spec.idempotent else 1
        result = await self._attempt(tool, call, ctx)
        for _ in range(attempts - 1):
            if result.ok or result.error_code is ToolErrorCode.TIMEOUT:
                break
            await asyncio.sleep(tool.spec.retry.backoff_seconds)
            result = await self._attempt(tool, call, ctx)
        return result
