"""On-behalf-of tokens: OAuth 2.0 Token Exchange (RFC 8693) against Project 0's Keycloak.

A tool that calls a downstream service for a user exchanges the user's token for a new one that
is valid **only** for that service (``aud``) and carries the user's identity (``sub``, groups).
The user's original token is never forwarded, and the platform's own service account is never
used to act for a user ("acts with the user's rights, never broader").
"""

from __future__ import annotations

import contextvars
import hashlib
import time
from dataclasses import dataclass

import httpx

from agent_tools.secrets import SecretResolver, maybe_secret

GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS = "urn:ietf:params:oauth:token-type:access_token"


class TokenExchangeError(PermissionError):
    pass


#: The verified bearer token of the user the current request acts for. Set by the edge (the MCP
#: gateway, the agent API) for the duration of one call; never stored in run state, checkpoints,
#: prompts or traces.
subject_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "subject_token", default=None
)


@dataclass(frozen=True)
class ExchangedToken:
    access_token: str
    audience: str
    expires_at: float


class TokenExchanger:
    """Exchanges subject tokens for audience-restricted tokens; caches until shortly before expiry."""

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        secrets: SecretResolver,
        scope_prefix: str = "agent-platform:",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.token_url = token_url
        self.client_id = client_id
        self._secret_ref = client_secret
        self._secrets = secrets
        self.scope_prefix = scope_prefix
        self._client = httpx.AsyncClient(timeout=10, transport=transport)
        self._cache: dict[tuple[str, str], ExchangedToken] = {}

    async def exchange(self, subject_token: str, audience: str) -> ExchangedToken:
        key = (hashlib.sha256(subject_token.encode()).hexdigest(), audience)
        cached = self._cache.get(key)
        if cached and cached.expires_at - 30 > time.time():
            return cached
        secret = await maybe_secret(self._secret_ref, self._secrets)
        resp = await self._client.post(
            self.token_url,
            auth=(self.client_id, secret or ""),
            data={
                "grant_type": GRANT,
                "subject_token": subject_token,
                "subject_token_type": ACCESS,
                "requested_token_type": ACCESS,
                "audience": audience,
                "scope": f"{self.scope_prefix}{audience}",
            },
        )
        if resp.status_code != 200:
            detail = (
                resp.json().get("error_description", resp.text[:200])
                if resp.headers.get("content-type", "").startswith("application/json")
                else resp.text[:200]
            )
            raise TokenExchangeError(f"token exchange for audience '{audience}' refused: {detail}")
        doc = resp.json()
        token = ExchangedToken(
            doc["access_token"], audience, time.time() + float(doc.get("expires_in", 60))
        )
        self._cache[key] = token
        return token

    async def aclose(self) -> None:
        await self._client.aclose()
