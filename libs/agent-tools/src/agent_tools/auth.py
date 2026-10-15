"""Verify Keycloak (OIDC) access tokens for platform-hosted MCP servers (Steps 7-8)."""

from __future__ import annotations

import asyncio
from typing import Any

import jwt
from mcp.server.auth.provider import AccessToken


class KeycloakTokenVerifier:
    """Checks signature (JWKS), issuer, expiry and, if set, audience. Implements MCP's TokenVerifier."""

    def __init__(
        self,
        issuer: str,
        audience: str | None = None,
        jwks_url: str | None = None,
        leeway: int = 30,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.leeway = leeway
        self._jwks = jwt.PyJWKClient(
            jwks_url or f"{self.issuer}/protocol/openid-connect/certs",
            cache_keys=True,
            lifespan=300,
        )

    def decode(self, token: str) -> dict[str, Any]:
        key = self._jwks.get_signing_key_from_jwt(token).key
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256", "ES256"],
            issuer=self.issuer,
            audience=self.audience,
            leeway=self.leeway,
            options={"require": ["exp", "iat", "iss"], "verify_aud": self.audience is not None},
        )
        return claims

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await asyncio.to_thread(self.decode, token)
        except jwt.PyJWTError:
            return None
        aud = claims.get("aud")
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or claims.get("client_id") or ""),
            scopes=str(claims.get("scope", "")).split(),
            expires_at=int(claims["exp"]),
            resource=aud if isinstance(aud, str) else None,
            subject=claims.get("sub"),
            claims={
                k: claims[k]
                for k in ("iss", "preferred_username", "groups", "realm_access", "tenant", "act")
                if k in claims
            },
        )


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
