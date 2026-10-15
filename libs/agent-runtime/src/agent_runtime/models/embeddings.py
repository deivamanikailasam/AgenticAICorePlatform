"""``embedder.gateway``: embeddings from the Project 0 LiteLLM gateway (Step 15).

The ``embedder`` role resolves like any model role (spec ``models:``, then ``platform.yaml``) and
uses its own virtual key, so the gateway attributes and budgets embedding calls too. Texts are
sent in batches; vectors come back in input order.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_contracts.interfaces import Embedder
from agent_contracts.models import RunContext

from agent_runtime.models.gateway import GatewayModelClient


class GatewayEmbedder(Embedder):
    component_status = "implemented"

    def __init__(
        self,
        client: GatewayModelClient | None = None,
        model: str = "embedder",
        batch_size: int = 64,
    ) -> None:
        self.client = client or GatewayModelClient()
        self.model = model
        self.batch_size = batch_size

    async def embed(self, texts: Sequence[str], ctx: RunContext) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = list(texts[i : i + self.batch_size])
            out += await self.client.embeddings(batch, self.model, ctx.run_id)
        return out
