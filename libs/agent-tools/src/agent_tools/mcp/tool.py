from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from agent_contracts.interfaces import Tool
from agent_contracts.models import RunContext, ToolSpec

if TYPE_CHECKING:
    from agent_tools.mcp.client import McpClientPool


class McpTool(Tool):
    """A tool discovered on an allowlisted MCP server; calls go through the pool."""

    component_status = "implemented"

    def __init__(self, spec: ToolSpec, pool: McpClientPool) -> None:
        self._spec = spec
        self._pool = pool

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        assert self._spec.ref is not None
        return await self._pool.call(self._spec.ref, dict(arguments), ctx)
