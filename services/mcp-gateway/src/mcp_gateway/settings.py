from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    """All configuration comes from the environment (12-factor); secrets only as references."""

    host: str = field(default_factory=lambda: _env("MCP_GATEWAY_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("MCP_GATEWAY_PORT", "8090")))
    public_url: str = field(
        default_factory=lambda: _env("MCP_GATEWAY_PUBLIC_URL", "http://127.0.0.1:8090")
    )
    # the issuer tokens carry (as users' clients see Keycloak) ...
    issuer: str = field(
        default_factory=lambda: (
            f"{_env('P0_KEYCLOAK_PUBLIC_URL', _env('P0_KEYCLOAK_URL', 'http://127.0.0.1:8080'))}"
            f"/realms/{_env('P0_KEYCLOAK_REALM', 'rag-platform')}"
        )
    )
    # ... and where this service reaches Keycloak (inside the Docker network) for keys and exchange
    internal_issuer: str = field(
        default_factory=lambda: (
            f"{_env('P0_KEYCLOAK_URL', 'http://127.0.0.1:8080')}/realms/"
            f"{_env('P0_KEYCLOAK_REALM', 'rag-platform')}"
        )
    )
    audience: str = field(default_factory=lambda: _env("MCP_GATEWAY_AUDIENCE", "agent-platform"))
    opa_url: str = field(default_factory=lambda: _env("P0_OPA_URL", "http://127.0.0.1:8181"))
    pdp: str = field(default_factory=lambda: _env("MCP_GATEWAY_PDP", "opa"))
    cerbos_url: str = field(
        default_factory=lambda: _env("AGENT_CERBOS_URL", "http://127.0.0.1:3592")
    )
    openfga_url: str = field(
        default_factory=lambda: _env("AGENT_OPENFGA_URL", "http://127.0.0.1:8085")
    )
    openfga_store: str = field(
        default_factory=lambda: _env("AGENT_OPENFGA_STORE", "agent-platform")
    )
    openfga_key: str = field(
        default_factory=lambda: _env(
            "AGENT_OPENFGA_KEY", "${secret:openfga/agent-platform#preshared_key}"
        )
    )
    openfga_rules: str = field(
        default_factory=lambda: _env("AGENT_OPENFGA_RULES", "policies/openfga/relations.yaml")
    )
    rag_core_url: str = field(
        default_factory=lambda: _env("P0_RAG_CORE_URL", "http://127.0.0.1:8300")
    )
    client_id: str = field(
        default_factory=lambda: _env("AGENT_PLATFORM_CLIENT_ID", "agent-platform")
    )
    client_secret: str = field(
        default_factory=lambda: _env(
            "AGENT_PLATFORM_CLIENT_SECRET", "${secret:keycloak/agent-platform#client_secret}"
        )
    )
    valkey_url: str = field(default_factory=lambda: _env("P0_VALKEY_URL", ""))
    # per tool, tenant and client, for people calling through the gateway; agents' own limits
    # come from their spec (tools[].rate_limit_per_minute)
    rate_limit_per_minute: int = field(
        default_factory=lambda: int(_env("MCP_GATEWAY_RATE_LIMIT_PER_MINUTE", "120"))
    )
    proxy_servers: tuple[str, ...] = field(
        default_factory=lambda: tuple(s for s in _env("MCP_GATEWAY_PROXY", "").split(",") if s)
    )
    allowed_hosts: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            _env("MCP_GATEWAY_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,mcp-gateway:*").split(",")
        )
    )

    @property
    def token_url(self) -> str:
        return f"{self.internal_issuer}/protocol/openid-connect/token"
