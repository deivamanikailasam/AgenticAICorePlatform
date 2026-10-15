"""Who is calling: turn a verified Keycloak token into a RunContext and an OPA subject."""

from __future__ import annotations

from typing import Any

from agent_contracts.models import RunContext
from agent_tools.auth import tenant_of
from mcp.server.auth.provider import AccessToken

__all__ = ["context_for", "tenant_of"]


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
