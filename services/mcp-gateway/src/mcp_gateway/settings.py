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
    issuer: str = field(
        default_factory=lambda: (
            f"{_env('P0_KEYCLOAK_URL', 'http://127.0.0.1:8080')}/realms/{_env('P0_KEYCLOAK_REALM', 'rag-platform')}"
        )
    )
    audience: str = field(default_factory=lambda: _env("MCP_GATEWAY_AUDIENCE", "agent-platform"))
    opa_url: str = field(default_factory=lambda: _env("P0_OPA_URL", "http://127.0.0.1:8181"))
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
        return f"{self.issuer}/protocol/openid-connect/token"
