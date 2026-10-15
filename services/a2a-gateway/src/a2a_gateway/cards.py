"""Agent Cards for published agents: skills, endpoints for each binding, the Keycloak security
scheme, signed with the platform's key so callers can check who published them."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    OpenIdConnectSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from agent_spec import AgentSpec

SCHEME = "keycloak"


def agent_card(
    spec: AgentSpec,
    base_url: str,
    issuer: str,
    grpc_url: str | None = None,
) -> AgentCard:
    root = f"{base_url.rstrip('/')}/agents/{spec.name}"
    interfaces = [
        AgentInterface(protocol_binding="JSONRPC", url=f"{root}/jsonrpc"),
        AgentInterface(protocol_binding="HTTP+JSON", url=f"{root}/rest"),
    ]
    if grpc_url:
        interfaces.append(AgentInterface(protocol_binding="GRPC", url=grpc_url))
    return AgentCard(
        name=spec.name,
        description=spec.description or spec.name,
        version=spec.version,
        provider=AgentProvider(organization=spec.owner, url=base_url),
        supported_interfaces=interfaces,
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id=spec.name,
                name=spec.name,
                description=spec.description or spec.name,
                tags=[spec.pattern.value, f"risk:{spec.risk_tier.value}"],
            )
        ],
        security_schemes={
            SCHEME: SecurityScheme(
                open_id_connect_security_scheme=OpenIdConnectSecurityScheme(
                    open_id_connect_url=f"{issuer}/.well-known/openid-configuration",
                    description="Keycloak access token for the agent-platform audience",
                )
            )
        },
        security_requirements=[SecurityRequirement(schemes={SCHEME: StringList(list=[])})],
    )


def signed(card: AgentCard, signer: Callable[[Any], Any]) -> AgentCard:
    result: AgentCard = signer(card)
    return result
