"""``rag-core/search``: Project 0's governed retrieval, exposed to agents as a tool (Step 8).

The call is made **on behalf of the user**: the user's token is exchanged for one restricted to
the ``rag-core`` audience, so rag-core applies the user's own document permissions. If there is
no user (no subject token), the tool refuses rather than falling back to a service identity.

Assumed rag-core API (VERIFY-ON-MAC against ~/dev/rag-platform):
``POST {base}/v1/search`` ``{"query", "k", "architecture"}`` →
``{"results": [{"id", "title", "snippet", "score", "source", "citation"}]}``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import httpx
from agent_contracts.interfaces import Tool
from agent_contracts.models import DataClass, RunContext, SideEffect, ToolSpec

from agent_tools.identity import TokenExchanger, subject_token

ARCHITECTURES = ["naive", "corrective", "adaptive", "graph", "sql"]


class RagCoreSearchTool(Tool):
    """Search company documents through Project 0's rag-core, with the caller's own permissions."""

    example_arguments: ClassVar[dict[str, Any]] = {"query": "refund policy"}

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8300",
        exchanger: TokenExchanger | None = None,
        audience: str = "rag-core",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.exchanger = exchanger
        self.audience = audience
        self._client = httpx.AsyncClient(timeout=30, transport=transport)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search",
            ref="mcp://rag-core/search",
            version="1.0.0",
            description="Search company documents. Returns passages with citations. Only documents the user may read are returned.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                    "architecture": {"type": "string", "enum": ARCHITECTURES, "default": "naive"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "results": {
                        "type": "array",
                        "items": {"type": "object", "required": ["id", "snippet", "citation"]},
                    }
                },
                "required": ["results"],
            },
            side_effect=SideEffect.READ,
            idempotent=True,
            timeout_seconds=30,
            data_classification=DataClass.CONFIDENTIAL,
            owner="rag-platform",
        )

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        user_token = subject_token.get()
        if not user_token:
            raise PermissionError("rag-core search must run on behalf of a user (no subject token)")
        if self.exchanger is None:
            raise PermissionError(
                "rag-core search needs a token exchanger; user tokens are never forwarded"
            )
        token = await self.exchanger.exchange(user_token, self.audience)
        resp = await self._client.post(
            f"{self.base_url}/v1/search",
            json={
                "query": arguments["query"],
                "k": arguments.get("k", 5),
                "architecture": arguments.get("architecture", "naive"),
            },
            headers={
                "Authorization": f"Bearer {token.access_token}",
                "X-Request-Tenant": ctx.tenant_id,
            },
        )
        resp.raise_for_status()
        return {"results": resp.json().get("results", [])}
