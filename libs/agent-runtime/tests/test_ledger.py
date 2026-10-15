"""The usage ledger records whose gateway key paid for each call (Step 26)."""

import httpx
from agent_contracts.models import ChatRequest, Message, Role
from agent_runtime.models import GatewayModelClient
from agent_spec.platform import load_platform


def gateway(handler: object) -> GatewayModelClient:
    return GatewayModelClient(
        load_platform(),
        keys={"planner": "sk-planner", "default": "sk-default"},
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


async def test_rows_keep_cost_in_usd_the_response_id_and_the_paying_key() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(
            200,
            json={
                "id": f"chatcmpl-{len(seen)}",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
            headers={"x-litellm-response-cost": "0.00005"},
        )

    gw = gateway(handler)
    for model in ("planner", "judge"):  # judge has no key of its own: the default key pays
        await gw.chat(
            ChatRequest(
                model=model,
                messages=(Message(role=Role.USER, content="hi"),),
                metadata={"run_id": "r"},
            )
        )
    rows = gw.ledger.rows("r")
    assert [(r.role, r.key, r.call_id, r.cost_usd) for r in rows] == [
        ("planner", "planner", "chatcmpl-1", 0.00005),
        ("judge", "default", "chatcmpl-2", 0.00005),
    ]
    assert seen == ["Bearer sk-planner", "Bearer sk-default"]
    assert rows[0].usage.cost_aud == round(0.00005 * load_platform().currency.usd_to_aud, 8)
