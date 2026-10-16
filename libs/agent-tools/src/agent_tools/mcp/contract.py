"""The tool contract for an MCP server (Step 33): what the platform needs from every server it
allows, checked against the real server through the real client.

Subclass it per server, give the server's configuration and one example call per tool:

    class TestErp(McpToolContract):
        server = McpServer(name="erp", transport="stdio", command=..., side_effects={...})
        examples = {"get_invoice": {"invoice_id": "INV-1001"}, ...}

Checks: every allowed tool is discovered, namespaced and has a valid input schema; every tool has
an example (a new tool can't go untested); each example succeeds through the platform executor
and its output is JSON and matches the tool's output schema; arguments that break the schema are
refused before the server sees them; side effects follow the platform's declarations (untrusted
servers' own annotations are not believed); tools off the allowlist are neither offered nor
callable.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from datetime import date
from typing import Any, ClassVar

import jsonschema
import pytest
from agent_contracts.models import RunContext, SideEffect, ToolCall, ToolErrorCode, ToolSpec
from agent_spec.platform import Currency, Mcp, McpServer, Models, PlatformConfig

from agent_tools.executor import PlatformToolExecutor
from agent_tools.mcp.client import McpClientPool, McpServerRefusedError, model_name
from agent_tools.registry import InMemoryToolRegistry

CTX = RunContext(tenant_id="contract", user_id="contract", agent_id="contract")


def _platform(server: McpServer) -> PlatformConfig:
    roles = {r: "m" for r in ("default", "planner", "executor", "router", "judge", "summariser")}
    return PlatformConfig(
        models=Models(roles=roles),
        currency=Currency(usd_to_aud=1.5, source="contract", as_of=date(2026, 10, 1)),
        mcp=Mcp(servers=[server]),
    )


class McpToolContract:
    server: ClassVar[McpServer]
    #: one valid call per tool the server offers (tool name without the server prefix)
    examples: ClassVar[Mapping[str, Mapping[str, Any]]]

    @pytest.fixture
    async def pool(self) -> AsyncIterator[McpClientPool]:
        p = McpClientPool(_platform(self.server))
        yield p
        await p.aclose()

    async def _specs(self, pool: McpClientPool) -> dict[str, ToolSpec]:
        prefix = f"mcp://{self.server.name}/"
        return {
            (s.ref or "").removeprefix(prefix): s for s in await pool.discover(self.server.name)
        }

    async def test_every_allowed_tool_is_discovered_with_a_valid_schema(
        self, pool: McpClientPool
    ) -> None:
        specs = await self._specs(pool)
        assert specs, f"{self.server.name} offers no tools"
        for tool, spec in specs.items():
            assert spec.name == model_name(self.server.name, tool)
            assert spec.ref == f"mcp://{self.server.name}/{tool}"
            jsonschema.Draft202012Validator.check_schema(spec.input_schema)
            if spec.output_schema:
                jsonschema.Draft202012Validator.check_schema(spec.output_schema)
        if self.server.allowed_tools:
            assert set(specs) <= set(self.server.allowed_tools)

    async def test_every_tool_has_an_example(self, pool: McpClientPool) -> None:
        missing = sorted(set(await self._specs(pool)) - set(self.examples))
        assert not missing, f"add an example call for: {', '.join(missing)}"

    async def test_examples_succeed_through_the_executor(self, pool: McpClientPool) -> None:
        reg = InMemoryToolRegistry()
        await pool.register_all(reg, self.server.name)
        executor = PlatformToolExecutor(reg)
        specs = await self._specs(pool)
        for tool, arguments in self.examples.items():
            spec = specs[tool]
            jsonschema.validate(dict(arguments), spec.input_schema)
            result = await executor.execute(
                ToolCall(name=spec.name, arguments=dict(arguments), idempotency_key=f"c-{tool}"),
                CTX,
            )
            assert result.ok, (tool, result.error_code, result.error)
            json.dumps(result.output)  # the model and the audit trail get JSON
            if spec.output_schema:
                jsonschema.validate(result.output, spec.output_schema)

    async def test_invalid_arguments_are_refused_before_the_server(
        self, pool: McpClientPool
    ) -> None:
        reg = InMemoryToolRegistry()
        await pool.register_all(reg, self.server.name)
        executor = PlatformToolExecutor(reg)
        for spec in (await self._specs(pool)).values():
            required = spec.input_schema.get("required") or []
            if not required:
                continue
            result = await executor.execute(
                ToolCall(name=spec.name, arguments={}, idempotency_key=f"bad-{spec.name}"), CTX
            )
            assert result.error_code is ToolErrorCode.INVALID_ARGUMENTS, spec.name

    async def test_side_effects_follow_the_platform(self, pool: McpClientPool) -> None:
        declared = self.server.side_effects or {}
        for tool, spec in (await self._specs(pool)).items():
            if tool in declared:
                assert spec.side_effect is SideEffect(declared[tool]), tool
            elif not self.server.trusted:
                # an untrusted server can't talk its way into "read-only"
                assert spec.side_effect is SideEffect.WRITE, tool

    async def test_unlisted_tools_are_neither_offered_nor_callable(
        self, pool: McpClientPool
    ) -> None:
        if not self.server.allowed_tools:
            pytest.skip("the server has no tool allowlist")
        with pytest.raises(McpServerRefusedError, match="not on server"):
            await pool.call(f"mcp://{self.server.name}/not-on-the-allowlist", {}, CTX)
