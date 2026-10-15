"""``embedder.gateway``: embeddings through the gateway by role, in order, costed."""

import json
from datetime import date

import httpx
import respx
from agent_contracts.models import RunContext
from agent_runtime.models import GatewayModelClient
from agent_runtime.models.embeddings import GatewayEmbedder
from agent_spec.platform import Currency, Gateway, Models, PlatformConfig

CTX = RunContext(tenant_id="t1", user_id="u1", agent_id="a1", run_id="run-1")


def client() -> GatewayModelClient:
    roles = {r: "fast" for r in ("default", "planner", "executor", "router", "judge", "summariser")}
    return GatewayModelClient(
        PlatformConfig(
            gateway=Gateway(url="http://gw.test", max_retries=1),
            models=Models(roles={**roles, "embedder": "embed-small"}),
            currency=Currency(usd_to_aud=1.5, source="test", as_of=date(2026, 10, 1)),
        ),
        keys={"default": "sk-default", "embedder": "sk-embed"},
        backoff_seconds=0,
    )


@respx.mock
async def test_embeds_in_batches_by_role_in_input_order_and_records_cost() -> None:
    seen: list[dict] = []

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"auth": request.headers["authorization"], **body})
        data = [
            {"index": i, "embedding": [float(len(t)), 1.0]} for i, t in enumerate(body["input"])
        ][::-1]  # out of order on purpose
        return httpx.Response(
            200,
            json={"data": data, "usage": {"prompt_tokens": 4}},
            headers={"x-litellm-response-cost": "0.001"},
        )

    respx.post("http://gw.test/v1/embeddings").mock(side_effect=reply)
    gw = client()
    vectors = await GatewayEmbedder(gw, batch_size=2).embed(["a", "bb", "ccc"], CTX)
    assert vectors == [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]]
    assert [s["input"] for s in seen] == [["a", "bb"], ["ccc"]]
    assert {s["model"] for s in seen} == {"embed-small"}
    assert {s["auth"] for s in seen} == {"Bearer sk-embed"}
    assert gw.ledger.calls("run-1") == 2
    assert gw.ledger.total("run-1").cost_aud == 0.003
