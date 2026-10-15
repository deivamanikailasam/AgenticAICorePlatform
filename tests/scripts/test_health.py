import httpx
import pytest
import respx

from scripts.health import Endpoints, build_checks, render, run_checks


@pytest.fixture
def endpoints() -> Endpoints:
    return Endpoints.from_env(
        {
            "P0_LITELLM_URL": "http://gw",
            "P0_KEYCLOAK_URL": "http://kc",
            "P0_KEYCLOAK_REALM": "r",
            "P0_OPA_URL": "http://opa",
            "P0_OTEL_HEALTH_URL": "http://otel/",
        }
    )


async def test_defaults_bind_to_loopback() -> None:
    ep = Endpoints.from_env({})
    for value in (ep.litellm_url, ep.keycloak_url, ep.opa_url, ep.otel_health_url):
        assert "127.0.0.1" in value


@respx.mock
async def test_http_checks_pass_and_fail(endpoints: Endpoints) -> None:
    respx.get("http://gw/health/liveliness").respond(200, text="I'm alive!")
    respx.get("http://kc/realms/r/.well-known/openid-configuration").respond(
        200, json={"issuer": "http://kc/realms/r"}
    )
    respx.get("http://opa/health").respond(500)
    respx.get("http://otel/").respond(200)
    async with httpx.AsyncClient() as client:
        checks = build_checks(endpoints, client)
        http_only = {k: v for k, v in checks.items() if k not in {"postgres", "valkey"}}
        results = {r.name: r for r in await run_checks(http_only)}
    assert results["litellm-gateway"].ok
    assert results["keycloak"].detail == "issuer http://kc/realms/r"
    assert not results["opa"].ok
    assert "500" in results["opa"].detail
    assert results["otel-collector"].ok


@respx.mock
async def test_keycloak_without_issuer_fails(endpoints: Endpoints) -> None:
    respx.get("http://kc/realms/r/.well-known/openid-configuration").respond(200, json={})
    async with httpx.AsyncClient() as client:
        checks = {"keycloak": build_checks(endpoints, client)["keycloak"]}
        [result] = await run_checks(checks)
    assert not result.ok


async def test_timeout_is_reported_not_raised() -> None:
    import asyncio

    async def slow() -> str:
        await asyncio.sleep(1)
        return "never"

    [result] = await run_checks({"slow": slow}, per_check_seconds=0.01)
    assert not result.ok
    assert "TimeoutError" in result.detail
    assert "FAIL" in render([result])
