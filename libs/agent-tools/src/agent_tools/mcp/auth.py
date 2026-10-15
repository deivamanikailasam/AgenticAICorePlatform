"""HTTP clients for remote MCP servers: anonymous, static bearer, OAuth client credentials, or a
per-user on-behalf-of token (``bearer_override``, one short-lived connection per call)."""

from __future__ import annotations

import httpx2
from agent_spec.platform import McpServer
from mcp.client.auth import TokenStorage
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from agent_tools.secrets import SecretResolver, maybe_secret


class MemoryTokenStorage(TokenStorage):
    """Tokens live only in process memory: never on disk, never in logs."""

    def __init__(self) -> None:
        self._tokens: OAuthToken | None = None
        self._client: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self._tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._client = client_info


async def http_client_for(
    server: McpServer, secrets: SecretResolver, bearer_override: str | None = None
) -> httpx2.AsyncClient:
    timeout = httpx2.Timeout(server.timeout_seconds, read=max(server.timeout_seconds, 300))
    auth = server.auth
    if bearer_override is not None:
        return httpx2.AsyncClient(
            timeout=timeout, headers={"Authorization": f"Bearer {bearer_override}"}
        )
    if auth.kind == "bearer":
        token = await maybe_secret(auth.token, secrets)
        if not token:
            raise ValueError(f"MCP server {server.name}: bearer auth needs a token")
        return httpx2.AsyncClient(timeout=timeout, headers={"Authorization": f"Bearer {token}"})
    if auth.kind in ("oauth_client_credentials", "on_behalf_of"):
        # on_behalf_of: these credentials are used for tools/list only; calls use the user's token
        secret = await maybe_secret(auth.client_secret, secrets)
        if not (auth.client_id and secret and auth.issuer and server.url):
            raise ValueError(
                f"MCP server {server.name}: OAuth needs issuer, client_id, client_secret and url"
            )
        provider = ClientCredentialsOAuthProvider(
            server_url=server.url,
            storage=MemoryTokenStorage(),
            client_id=auth.client_id,
            client_secret=secret,
            scope=auth.scope,
            issuer=auth.issuer,
        )
        return httpx2.AsyncClient(timeout=timeout, auth=provider)
    return httpx2.AsyncClient(timeout=timeout)
