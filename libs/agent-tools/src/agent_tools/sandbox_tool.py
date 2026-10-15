"""``sandbox://python``: run model-written Python in the sandbox runner (Step 9).

The tool authenticates to the runner with the platform's own client-credentials token. Code
never runs in the agent's process. Output (stdout, stderr, files) is untrusted data.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx
from agent_contracts.interfaces import Tool
from agent_contracts.models import DataClass, RunContext, SideEffect, ToolSpec

from agent_tools.secrets import SecretResolver, maybe_secret


class ClientCredentials:
    """Fetches and caches the platform's service token (OAuth client credentials)."""

    def __init__(
        self, token_url: str, client_id: str, client_secret: str, secrets: SecretResolver
    ) -> None:
        self.token_url, self.client_id, self._secret, self._secrets = (
            token_url,
            client_id,
            client_secret,
            secrets,
        )
        self._token: tuple[str, float] | None = None

    async def token(self) -> str:
        if self._token and self._token[1] - 30 > time.time():
            return self._token[0]
        secret = await maybe_secret(self._secret, self._secrets)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                self.token_url,
                auth=(self.client_id, secret or ""),
                data={"grant_type": "client_credentials"},
            )
        resp.raise_for_status()
        doc = resp.json()
        self._token = (str(doc["access_token"]), time.time() + float(doc.get("expires_in", 60)))
        return self._token[0]


class SandboxPythonTool(Tool):
    """Run a short Python program in an isolated sandbox (no network) and return its output."""

    example_arguments: ClassVar[dict[str, Any]] = {"code": "print(sum(range(10)))"}

    def __init__(
        self,
        runner_url: str = "http://127.0.0.1:8095",
        credentials: ClientCredentials | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.runner_url = runner_url.rstrip("/")
        self.credentials = credentials
        self._client = httpx.AsyncClient(timeout=660, transport=transport)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="python",
            ref="sandbox://python",
            version="1.0.0",
            description=(
                "Run a short Python 3.12 program in an isolated sandbox with no network access. "
                "Input files are in /work/in; write output files to /work/out. Returns stdout, stderr and files."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "minLength": 1, "maxLength": 200_000},
                    "files": {"type": "object", "additionalProperties": {"type": "string"}},
                    "wall_seconds": {"type": "number", "minimum": 1, "maximum": 120, "default": 10},
                },
                "required": ["code"],
                "additionalProperties": False,
            },
            output_schema={"type": "object", "required": ["status"]},
            side_effect=SideEffect.READ,
            idempotent=False,
            timeout_seconds=180,
            data_classification=DataClass.INTERNAL,
            owner="agent-platform",
        )

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        headers = (
            {"Authorization": f"Bearer {await self.credentials.token()}"}
            if self.credentials
            else {}
        )
        body = {
            "code": arguments["code"],
            "files": dict(arguments.get("files", {})),
            "limits": {"wall_seconds": float(arguments.get("wall_seconds", 10))},
            "run_id": ctx.run_id,
            "tenant_id": ctx.tenant_id,
        }
        resp = await self._client.post(
            f"{self.runner_url}/v1/executions", json=body, headers=headers
        )
        resp.raise_for_status()
        doc = resp.json()
        result = doc["result"]
        return {
            "status": result["status"],
            "exit_code": result.get("exit_code"),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "truncated": result.get("truncated", False),
            "reasons": result.get("reasons", []),
            "artefacts": doc.get("artefacts", {}),
            "files": result.get("files", {}),
        }
