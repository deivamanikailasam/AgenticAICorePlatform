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
from typing import Any, Protocol

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


# ---------------------------------------------------------------- per-agent identities (Step 29)

#: The agent a tool call runs for. Set by the authorising executor for the duration of one call;
#: unset at the MCP gateway, where a person calls tools directly.
acting_agent: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "acting_agent", default=None
)


class Exchanger(Protocol):
    async def exchange(self, subject_token: str, audience: str) -> ExchangedToken: ...

    async def aclose(self) -> None: ...


class AgentIdentities:
    """Each agent's own Keycloak client, ``agent-<name>`` (created by ``just provision-keycloak``
    from the specs; its secret in OpenBao at ``keycloak/agent-<name>``).

    * ``service_token(agent)``: the agent's own service-account token (client credentials). It
      is what the agent logs in to OpenBao with (``bao_auth``), so OpenBao's policy for that
      agent decides which tool credentials it may obtain.
    * ``exchanger(agent)``: token exchange *as the agent*, for the second hop of delegation.
    """

    def __init__(
        self,
        token_url: str,
        secrets: SecretResolver,
        client_prefix: str = "agent-",
        scope_prefix: str = "agent-platform:",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.token_url = token_url
        self._secrets = secrets
        self.client_prefix = client_prefix
        self.scope_prefix = scope_prefix
        self._transport = transport
        self._http = httpx.AsyncClient(timeout=10, transport=transport)
        self._tokens: dict[str, tuple[str, float]] = {}
        self._exchangers: dict[str, TokenExchanger] = {}

    def client_id(self, agent: str) -> str:
        return f"{self.client_prefix}{agent}"

    def secret_ref(self, agent: str) -> str:
        return f"${{secret:keycloak/{self.client_id(agent)}#client_secret}}"

    async def service_token(self, agent: str) -> str:
        cached = self._tokens.get(agent)
        if cached and cached[1] - 30 > time.time():
            return cached[0]
        secret = await self._secrets.resolve(self.secret_ref(agent))
        resp = await self._http.post(
            self.token_url,
            auth=(self.client_id(agent), secret),
            data={"grant_type": "client_credentials"},
        )
        if resp.status_code != 200:
            raise TokenExchangeError(
                f"agent {agent}: Keycloak refused its service identity ({resp.status_code})"
            )
        doc = resp.json()
        self._tokens[agent] = (str(doc["access_token"]), time.time() + float(doc["expires_in"]))
        return self._tokens[agent][0]

    def exchanger(self, agent: str) -> TokenExchanger:
        if agent not in self._exchangers:
            self._exchangers[agent] = TokenExchanger(
                self.token_url,
                self.client_id(agent),
                self.secret_ref(agent),
                self._secrets,
                scope_prefix=self.scope_prefix,
                transport=self._transport,
            )
        return self._exchangers[agent]

    def bao_auth(self, agent: str) -> Any:
        """OpenBao JWT login as this agent (role ``agent-<name>``)."""
        from agent_tools.openbao import JwtAuth

        return JwtAuth(self.client_id(agent), lambda: self.service_token(agent))

    async def aclose(self) -> None:
        for ex in self._exchangers.values():
            await ex.aclose()
        await self._http.aclose()


class DelegatingExchanger:
    """Delegation in two down-scoping hops when a tool runs for an agent:

    1. the platform exchanges the user's token for one whose audience is the agent
       (``aud=agent-<name>``, still the user's ``sub`` and groups);
    2. the agent exchanges that for the downstream service (``aud=<service>``, ``azp=agent-<name>``).

    Each hop can only narrow: Keycloak refuses an audience the requesting client has no scope
    for, and a user token cannot go to an agent directly (the agent is not in its audience).
    Without an acting agent (the MCP gateway), it is the platform's single hop as before.
    """

    def __init__(self, platform: TokenExchanger, identities: AgentIdentities) -> None:
        self.platform = platform
        self.identities = identities

    async def exchange(self, subject_token: str, audience: str) -> ExchangedToken:
        agent = acting_agent.get()
        if agent is None:
            return await self.platform.exchange(subject_token, audience)
        hop = await self.platform.exchange(subject_token, self.identities.client_id(agent))
        return await self.identities.exchanger(agent).exchange(hop.access_token, audience)

    async def aclose(self) -> None:
        await self.platform.aclose()
        await self.identities.aclose()
