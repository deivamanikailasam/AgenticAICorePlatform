"""``just cost-reconcile``: the platform's ledger against LiteLLM's spend, per role key."""

import asyncio
import os
import uuid
from datetime import UTC, date, datetime

import httpx
import psycopg
import pytest
import respx

from scripts import cost_reconcile as cr


@respx.mock
def test_gateway_spend_sums_each_role_keys_daily_spend() -> None:
    respx.get("http://gw/key/list").respond(
        200,
        json={
            "keys": [
                {"key_alias": "agent-platform-planner", "token": "h1"},
                {"key_alias": "someone-else", "token": "h2"},
            ]
        },
    )
    logs = respx.get("http://gw/spend/logs").respond(
        200,
        json=[
            {"startTime": "2026-10-14", "spend": 0.25},
            {"startTime": "2026-10-15", "spend": 0.5},
        ],
    )
    with httpx.Client() as c:
        spend = cr.gateway_spend(c, "http://gw", "mk", date(2026, 10, 14), date(2026, 10, 15))
    assert spend == {"planner": 0.75}
    assert logs.calls[0].request.url.params["api_key"] == "h1"
    assert logs.calls[0].request.url.params["start_date"] == "2026-10-14"
    assert logs.calls[0].request.url.params["end_date"] == "2026-10-16"  # exclusive in LiteLLM


def test_compare_flags_differences_beyond_the_tolerance() -> None:
    rows = cr.compare({"planner": 1.0, "router": 0.2}, {"planner": 1.004, "judge": 0.1})
    by_role = {r.role: r for r in rows}
    assert by_role["planner"].ok(0.005)
    assert not by_role["router"].ok(0.005)  # the gateway saw nothing
    assert not by_role["judge"].ok(0.005)  # the platform recorded nothing
    assert "| planner | 1.000000 | 1.004000 | -0.004000 | yes |" in cr.render(
        rows, 0.005, 1.5, date(2026, 10, 1), date(2026, 10, 1)
    )


@pytest.mark.integration
async def test_platform_ledger_matches_litellm_spend_for_a_key() -> None:
    """Real calls through the stand-in gateway (priced mock models): the platform's ledger and
    LiteLLM's spend logs agree for the key that made them."""
    from agent_contracts.models import ChatRequest, Message, Role
    from agent_runtime.models import GatewayModelClient, PostgresUsageLedger
    from agent_spec.platform import load_platform

    dsn, master = os.environ.get("AGENT_DB_DSN"), os.environ.get("LITELLM_MASTER_KEY")
    if not dsn or not master:
        pytest.skip("needs AGENT_DB_DSN and LITELLM_MASTER_KEY")
    base = os.environ.get("P0_LITELLM_URL", "http://127.0.0.1:4000")
    role = f"recon{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(timeout=20) as ac:
        made = await ac.post(
            f"{base}/key/generate",
            headers={"Authorization": f"Bearer {master}"},
            json={"key_alias": f"agent-platform-{role}", "models": ["local-qwen-fast"]},
        )
        made.raise_for_status()
    key = made.json()["key"]
    platform = load_platform().model_copy(
        update={"gateway": load_platform().gateway.model_copy(update={"url": base})}
    )
    ledger = PostgresUsageLedger(dsn)
    gw = GatewayModelClient(platform, keys={"default": key}, ledger=ledger)
    run_id = f"it-{role}"
    for text in ("one", "two", "three"):
        await gw.chat(
            ChatRequest(
                model="local-qwen-fast",
                messages=(Message(role=Role.USER, content=text),),
                metadata={"run_id": run_id},
            )
        )
    with psycopg.connect(dsn) as conn:
        (platform_usd,) = conn.execute(
            "select coalesce(sum(cost_usd), 0) from agent_runtime.usage_ledger where run_id = %s",
            (run_id,),
        ).fetchone() or (0,)
    assert float(platform_usd) > 0, "the stand-in prices local-qwen-fast"
    assert {r.key for r in ledger.rows(run_id)} == {"default"}  # the key that paid
    today = datetime.now(UTC).date()
    gateway_usd = 0.0
    with httpx.Client(timeout=20) as c:
        for _ in range(36):  # LiteLLM writes spend logs in batches
            gateway_usd = await asyncio.to_thread(cr.gateway_spend, c, base, master, today, today)
            gateway_usd = gateway_usd.get(role, 0.0) if isinstance(gateway_usd, dict) else 0.0
            if gateway_usd > 0:
                break
            await asyncio.sleep(5)
    assert cr.Row(role, float(platform_usd), gateway_usd).ok(0.005), (platform_usd, gateway_usd)
