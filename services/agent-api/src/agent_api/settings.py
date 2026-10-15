"""Run API configuration, all from the environment (12-factor); secrets only as references."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


@dataclass(frozen=True)
class Settings:
    host: str = field(default_factory=lambda: _env("AGENT_API_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("AGENT_API_PORT", "8091")))
    # the issuer tokens carry (as users' browsers see Keycloak) ...
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
    audience: str = field(default_factory=lambda: _env("AGENT_API_AUDIENCE", "agent-platform"))
    agents_dir: str = field(default_factory=lambda: _env("AGENT_API_AGENTS_DIR", "agents"))
    pdp: str = field(default_factory=lambda: _env("AGENT_API_PDP", "opa"))
    opa_url: str = field(default_factory=lambda: _env("P0_OPA_URL", "http://127.0.0.1:8181"))
    cerbos_url: str = field(
        default_factory=lambda: _env("AGENT_CERBOS_URL", "http://127.0.0.1:3592")
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
    audit_file: str = field(
        default_factory=lambda: _env("AGENT_API_AUDIT_FILE", "/tmp/agent-api-audit.jsonl")  # noqa: S108
    )

    @property
    def token_url(self) -> str:
        return f"{self.internal_issuer}/protocol/openid-connect/token"

    @property
    def jwks_url(self) -> str:
        return f"{self.internal_issuer}/protocol/openid-connect/certs"
