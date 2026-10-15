"""The ``run_node`` activity: one plan node through the platform's node runner (Step 19).

Runs in the worker process, which holds the composition root (model client, authorising tool
executor, functions) for every plan it serves. Long nodes heartbeat so a dead worker is noticed
quickly and the node is retried on another worker; tool nodes keep their stable idempotency key
``<run>:<node>``, so a retried write is not repeated. Values larger than ``max_inline_bytes``
travel as references to the artefact store (S3 in production), because Temporal caps payload
size; references are resolved before a node sees its inputs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_contracts.models import ExecutablePlan, RunContext
from temporalio import activity

from agent_runtime.nodes import NodeRunner

REF = "$ref"


class ArtefactStore:  # the subset of agent_tools.artefacts used here
    async def put(self, key: str, data: bytes) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    async def get(self, uri: str) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class NodeActivities:
    runners: dict[str, tuple[ExecutablePlan, NodeRunner]] = field(default_factory=dict)
    artefacts: Any = None
    max_inline_bytes: int = 256 * 1024
    heartbeat_seconds: float = 5.0

    def serve(self, handle: str, plan: ExecutablePlan, runner: NodeRunner) -> None:
        self.runners[handle] = (plan, runner)

    async def _resolve(self, value: Any) -> Any:
        if isinstance(value, Mapping) and set(value) == {REF}:
            return json.loads(await self.artefacts.get(value[REF]))
        if isinstance(value, Mapping):
            return {k: await self._resolve(v) for k, v in value.items()}
        return value

    async def _offload(self, key: str, value: Any) -> Any:
        body = json.dumps(value, default=str).encode()
        if len(body) <= self.max_inline_bytes or self.artefacts is None:
            return json.loads(body)
        return {REF: await self.artefacts.put(key, body)}

    @activity.defn(name="run_node")
    async def run_node(self, req: dict[str, Any]) -> dict[str, Any]:
        plan, runner = self.runners[req["handle"]]
        node = plan.node(req["node_id"])
        ctx = RunContext.model_validate(req["ctx"])
        values = {k: await self._resolve(v) for k, v in req["values"].items()}

        async def beat() -> None:
            while True:
                await asyncio.sleep(self.heartbeat_seconds)
                activity.heartbeat(node.id)

        activity.heartbeat(node.id)  # every node reports once, even an instant one

        beating = asyncio.create_task(beat())
        try:
            result = await runner.run(node, values, ctx)
        finally:
            beating.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beating
        info = activity.info()
        key = f"temporal/{ctx.tenant_id}/{ctx.run_id}/{node.id}-{info.attempt}.json"
        return {
            "outcome": result.outcome,
            "value": await self._offload(key, result.value),
            "error": result.error,
        }
