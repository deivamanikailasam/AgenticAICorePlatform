from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    """All configuration comes from the environment (12-factor); secrets only as references."""

    host: str = field(default_factory=lambda: _env("A2A_GATEWAY_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("A2A_GATEWAY_PORT", "8092")))
    # gRPC binding (optional): one agent per gateway process, on its own port
    grpc_agent: str = field(default_factory=lambda: _env("A2A_GATEWAY_GRPC_AGENT", ""))
    grpc_port: int = field(default_factory=lambda: int(_env("A2A_GATEWAY_GRPC_PORT", "8093")))
    grpc_public: str = field(
        default_factory=lambda: _env("A2A_GATEWAY_GRPC_PUBLIC", "127.0.0.1:8093")
    )
    agents_dir: str = field(default_factory=lambda: _env("A2A_GATEWAY_AGENTS_DIR", "agents"))
    # where published agents actually run: the run API, called as the A2A caller
    api_url: str = field(default_factory=lambda: _env("AGENT_API_URL", "http://127.0.0.1:8091"))
    issuer: str = field(
        default_factory=lambda: (
            f"{_env('P0_KEYCLOAK_PUBLIC_URL', _env('P0_KEYCLOAK_URL', 'http://127.0.0.1:8080'))}"
            f"/realms/{_env('P0_KEYCLOAK_REALM', 'rag-platform')}"
        )
    )
    internal_issuer: str = field(
        default_factory=lambda: (
            f"{_env('P0_KEYCLOAK_URL', 'http://127.0.0.1:8080')}/realms/"
            f"{_env('P0_KEYCLOAK_REALM', 'rag-platform')}"
        )
    )
    audience: str = field(default_factory=lambda: _env("A2A_GATEWAY_AUDIENCE", "agent-platform"))
    pdp: str = field(default_factory=lambda: _env("A2A_GATEWAY_PDP", "opa"))
    opa_url: str = field(default_factory=lambda: _env("P0_OPA_URL", "http://127.0.0.1:8181"))
    cerbos_url: str = field(
        default_factory=lambda: _env("AGENT_CERBOS_URL", "http://127.0.0.1:3592")
    )
    audit_file: str = field(
        default_factory=lambda: _env("A2A_GATEWAY_AUDIT_FILE", "/tmp/a2a-gateway-audit.jsonl")  # noqa: S108
    )
