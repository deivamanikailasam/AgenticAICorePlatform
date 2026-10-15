import pytest
import respx
from agent_tools.identity import GRANT, TokenExchangeError, TokenExchanger
from agent_tools.secrets import EnvSecretResolver

URL = "http://kc/realms/r/protocol/openid-connect/token"


@respx.mock
async def test_exchanges_for_an_audience_restricted_token_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET__KEYCLOAK__AGENT_PLATFORM__CLIENT_SECRET", "s3cret")
    route = respx.post(URL).respond(200, json={"access_token": "downscoped", "expires_in": 300})
    ex = TokenExchanger(
        URL,
        "agent-platform",
        "${secret:keycloak/agent-platform#client_secret}",
        EnvSecretResolver(),
    )
    first = await ex.exchange("user-token", "rag-core")
    again = await ex.exchange("user-token", "rag-core")
    assert first.access_token == again.access_token == "downscoped"
    assert route.call_count == 1, "cached until shortly before expiry"
    form = dict(x.split("=", 1) for x in route.calls[0].request.content.decode().split("&"))
    assert form["grant_type"] == GRANT.replace(":", "%3A")
    assert form["audience"] == "rag-core"
    assert form["scope"] == "agent-platform%3Arag-core"
    assert route.calls[0].request.headers["authorization"].startswith("Basic ")


@respx.mock
async def test_refusal_is_a_permission_error() -> None:
    respx.post(URL).respond(
        400,
        json={
            "error": "invalid_request",
            "error_description": "Requested audience not available: x",
        },
    )
    ex = TokenExchanger(URL, "agent-platform", "plain-secret", EnvSecretResolver())
    with pytest.raises(TokenExchangeError, match="audience not available"):
        await ex.exchange("user-token", "x")


async def test_secret_resolver_reports_missing_secrets() -> None:
    with pytest.raises(KeyError, match="SECRET__VAULT__X__Y"):
        await EnvSecretResolver().resolve("${secret:vault/x#y}")
