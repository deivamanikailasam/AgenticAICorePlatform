"""``PlatformToolExecutor``: the one place every tool call goes through.

Order of checks for each call:
1. find the tool (``not_found``);
2. validate arguments against the input schema, *before* anything runs (``invalid_arguments``);
3. ``write`` and ``irreversible`` tools must carry an idempotency key;
4. a repeated key returns the first call's result without running the tool again;
5. run with the tool's timeout; retry only idempotent, non-irreversible tools, per their policy;
6. validate the output schema; spill outputs larger than ``max_inline_bytes`` to the artefact store.

Policy (OPA, Step 10), guardrails (Step 27) and approvals (Step 28) wrap this executor; it never
grants permissions itself. Tool results are always ``Trust.UNTRUSTED``.
"""

from __future__ import annotations

import asyncio
import json
import time

import jsonschema
from agent_contracts.interfaces import Tool, ToolExecutor, ToolRegistry
from agent_contracts.models import RunContext, SideEffect, ToolCall, ToolErrorCode, ToolResult

from agent_tools.artefacts import ArtefactStore, MemoryArtefactStore
from agent_tools.idempotency import IdempotencyStore, MemoryIdempotencyStore

PREVIEW_CHARS = 2000


class PlatformToolExecutor(ToolExecutor):
    """Validates, de-duplicates, times out, retries safely and spills large outputs to S3."""

    def __init__(
        self,
        registry: ToolRegistry,
        idempotency: IdempotencyStore | None = None,
        artefacts: ArtefactStore | None = None,
        max_inline_bytes: int = 64 * 1024,
        idempotency_ttl_seconds: int = 24 * 3600,
        duplicate_wait_seconds: float = 5.0,
    ) -> None:
        self.registry = registry
        self.idempotency = idempotency or MemoryIdempotencyStore()
        self.artefacts = artefacts or MemoryArtefactStore()
        self.max_inline_bytes = max_inline_bytes
        self.ttl = idempotency_ttl_seconds
        self.duplicate_wait = duplicate_wait_seconds

    @staticmethod
    def _fail(call: ToolCall, code: ToolErrorCode, message: str, started: float) -> ToolResult:
        return ToolResult(
            call_id=call.id,
            tool=call.name,
            ok=False,
            error_code=code,
            error=message,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        started = time.perf_counter()
        try:
            tool = await self.registry.get(call.name)
        except KeyError as exc:
            return self._fail(
                call, ToolErrorCode.NOT_FOUND, f"unknown tool {call.name}: {exc}", started
            )
        spec = tool.spec
        try:
            jsonschema.validate(call.arguments, spec.input_schema)
        except jsonschema.ValidationError as exc:
            where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
            return self._fail(
                call, ToolErrorCode.INVALID_ARGUMENTS, f"{where}: {exc.message}", started
            )
        if spec.side_effect is not SideEffect.READ and not call.idempotency_key:
            return self._fail(
                call,
                ToolErrorCode.INVALID_ARGUMENTS,
                f"{spec.side_effect.value} tools need an idempotency key",
                started,
            )

        key = (
            f"{ctx.tenant_id}:{spec.name}:{call.idempotency_key}" if call.idempotency_key else None
        )
        if key is not None and not await self.idempotency.claim(key, self.ttl):
            return await self._replay(call, key, started)

        result = await self._run(tool, call, ctx, started)
        if key is not None:
            if result.ok or spec.side_effect is SideEffect.IRREVERSIBLE:
                # an irreversible call's outcome is kept even on failure: never re-run it automatically
                await self.idempotency.put(key, result, self.ttl)
            else:
                await self.idempotency.release(key)
        return result

    async def _replay(self, call: ToolCall, key: str, started: float) -> ToolResult:
        deadline = time.monotonic() + self.duplicate_wait
        while True:
            first = await self.idempotency.get(key)
            if first is not None:
                return first.model_copy(update={"call_id": call.id})
            if not await self.idempotency.pending(key):
                # the first call may have stored its result between the two reads above
                first = await self.idempotency.get(key)
                if first is not None:
                    return first.model_copy(update={"call_id": call.id})
                return self._fail(
                    call,
                    ToolErrorCode.FAILED,
                    "the first call with this idempotency key did not finish; retry",
                    started,
                )
            if time.monotonic() > deadline:
                return self._fail(
                    call,
                    ToolErrorCode.FAILED,
                    "a call with this idempotency key is still in progress",
                    started,
                )
            await asyncio.sleep(0.05)

    async def _attempt(
        self, tool: Tool, call: ToolCall, ctx: RunContext, started: float
    ) -> ToolResult:
        try:
            output = await asyncio.wait_for(
                tool.invoke(call.arguments, ctx), tool.spec.timeout_seconds
            )
        except TimeoutError:
            return self._fail(
                call,
                ToolErrorCode.TIMEOUT,
                f"timed out after {tool.spec.timeout_seconds}s",
                started,
            )
        except Exception as exc:
            return self._fail(call, ToolErrorCode.FAILED, f"{type(exc).__name__}: {exc}", started)
        if tool.spec.output_schema is not None:
            try:
                jsonschema.validate(output, tool.spec.output_schema)
            except jsonschema.ValidationError as exc:
                return self._fail(
                    call,
                    ToolErrorCode.FAILED,
                    f"output did not match the tool's schema: {exc.message}",
                    started,
                )
        return await self._package(call, ctx, output, started)

    async def _run(self, tool: Tool, call: ToolCall, ctx: RunContext, started: float) -> ToolResult:
        spec = tool.spec
        retryable = spec.idempotent and spec.side_effect is not SideEffect.IRREVERSIBLE
        attempts = spec.retry.max_attempts if retryable else 1
        result = await self._attempt(tool, call, ctx, started)
        for n in range(1, attempts):
            if result.ok or result.error_code is ToolErrorCode.TIMEOUT:
                break
            await asyncio.sleep(spec.retry.backoff_seconds * 2 ** (n - 1))
            result = await self._attempt(tool, call, ctx, started)
        return result

    async def _package(
        self, call: ToolCall, ctx: RunContext, output: object, started: float
    ) -> ToolResult:
        body = json.dumps(output, default=str).encode()
        uri = None
        if len(body) > self.max_inline_bytes:
            key = f"tool-outputs/{ctx.tenant_id}/{ctx.run_id}/{call.id}.json"
            uri = await self.artefacts.put(key, body)
            output = {
                "truncated": True,
                "bytes": len(body),
                "preview": body[:PREVIEW_CHARS].decode(errors="replace"),
                "artefact_uri": uri,
            }
        return ToolResult(
            call_id=call.id,
            tool=call.name,
            ok=True,
            output=output,
            artefact_uri=uri,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
