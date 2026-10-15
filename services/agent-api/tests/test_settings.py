"""Inside Docker, tokens carry the public issuer while keys are fetched over the internal URL."""

import pytest
from agent_api.settings import Settings
from mcp_gateway.settings import Settings as GatewaySettings


@pytest.mark.parametrize("cls", [Settings, GatewaySettings])
def test_public_issuer_and_internal_endpoints(cls: type, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("P0_KEYCLOAK_URL", "http://keycloak:8080")
    monkeypatch.setenv("P0_KEYCLOAK_PUBLIC_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("P0_KEYCLOAK_REALM", "rag-platform")
    s = cls()
    assert s.issuer == "http://127.0.0.1:8080/realms/rag-platform"
    assert s.token_url == "http://keycloak:8080/realms/rag-platform/protocol/openid-connect/token"


def test_without_a_public_url_both_are_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("P0_KEYCLOAK_PUBLIC_URL", raising=False)
    monkeypatch.setenv("P0_KEYCLOAK_URL", "http://127.0.0.1:8080")
    s = Settings()
    assert s.jwks_url == f"{s.issuer}/protocol/openid-connect/certs"
