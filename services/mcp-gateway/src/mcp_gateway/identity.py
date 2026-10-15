"""Who is calling: turn a verified Keycloak token into a RunContext and an OPA subject."""

from __future__ import annotations

from typing import Any

from agent_contracts.models import RunContext
from mcp.server.auth.provider import AccessToken


def tenant_of(groups: list[str], default: str = "default") -> str:
    """Tenants are the top level of Keycloak group paths: ``/acme/finance`` → ``acme``.

    VERIFY-ON-MAC: align with Project 0's tenant claim convention if it differs.
    """
    tenants = sorted({g.strip("/").split("/")[0] for g in groups if g.strip("/")})
    if len(tenants) > 1:
        raise PermissionError(
            f"user belongs to several tenants ({', '.join(tenants)}); pick one explicitly"
        )
    return tenants[0] if tenants else default


def context_for(token: AccessToken, run_id: str | None = None) -> tuple[RunContext, dict[str, Any]]:
    claims = token.claims or {}
    groups = [str(g) for g in claims.get("groups", [])]
    roles = [str(r) for r in claims.get("realm_access", {}).get("roles", [])]
    user = str(claims.get("preferred_username") or token.subject or "unknown")
    kwargs: dict[str, Any] = {"run_id": run_id} if run_id else {}
    ctx = RunContext(
        tenant_id=tenant_of(groups),
        user_id=user,
        agent_id=f"mcp-client:{token.client_id}",
        **kwargs,
    )
    subject = {
        "authenticated": True,
        "user": user,
        "client_id": token.client_id,
        "groups": groups,
        "roles": roles,
    }
    return ctx, subject
