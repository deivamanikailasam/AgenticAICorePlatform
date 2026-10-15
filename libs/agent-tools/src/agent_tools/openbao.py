"""OpenBao (and HashiCorp Vault): platform secrets and short-lived tool credentials (Step 29).

Two jobs, two classes:

* ``OpenBaoSecretResolver`` reads the platform's own static secrets (a Keycloak client secret,
  a LiteLLM key) from the KV v2 mount, for ``${secret:path#key}`` references.
* ``OpenBaoCredentialBroker`` issues **dynamic** credentials for tools from a secrets engine's
  ``creds`` endpoint (``agent-db/creds/tool-reader`` → a new Postgres user that lives for
  minutes), and revokes them after the call. Each agent logs in with its own identity (its
  Keycloak service-account token, through OpenBao's JWT auth), so an agent can only obtain the
  credentials its OpenBao policy names.

Both talk to the HTTP API (``/v1/...``), which OpenBao and Vault share; ``VaultCredentialBroker``
is the same class under Vault's name. Tokens and secret values are never logged or returned in
errors.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from agent_contracts.interfaces import CredentialBroker
from agent_contracts.models import Credential, RunContext, utcnow
from pydantic import SecretStr

from agent_tools.secrets import SECRET_REF, SecretResolver, maybe_secret

DEFAULT_KV_MOUNT = "agent-platform"
SOURCE_PREFIX = "openbao:"


class OpenBaoError(RuntimeError):
    pass


# ---------------------------------------------------------------- authentication


class OpenBaoAuth:
    """How a client gets its OpenBao token. ``login`` returns (token, seconds it is valid)."""

    async def login(self, http: httpx.AsyncClient, url: str) -> tuple[str, float]:
        raise NotImplementedError


@dataclass
class TokenAuth(OpenBaoAuth):
    """A token given from outside (dev: the stand-in's root token; never in production)."""

    token: str
    secrets: SecretResolver | None = None

    async def login(self, http: httpx.AsyncClient, url: str) -> tuple[str, float]:
        value = await maybe_secret(self.token, self.secrets) if self.secrets else self.token
        return value or "", float("inf")


async def _login(
    http: httpx.AsyncClient, url: str, path: str, body: dict[str, Any]
) -> tuple[str, float]:
    resp = await http.post(f"{url}/v1/auth/{path}/login", json=body)
    if resp.status_code != 200:
        raise PermissionError(
            f"OpenBao login at auth/{path} refused ({resp.status_code}): {_errors(resp)}"
        )
    auth = resp.json()["auth"]
    return str(auth["client_token"]), float(auth.get("lease_duration") or 0) or float("inf")


@dataclass
class AppRoleAuth(OpenBaoAuth):
    """The platform's own identity outside Kubernetes: a role id and a secret id."""

    role_id: str
    secret_id: str
    secrets: SecretResolver | None = None
    mount: str = "approle"

    async def login(self, http: httpx.AsyncClient, url: str) -> tuple[str, float]:
        secret_id = (
            await maybe_secret(self.secret_id, self.secrets) if self.secrets else self.secret_id
        )
        return await _login(
            http, url, self.mount, {"role_id": self.role_id, "secret_id": secret_id}
        )


@dataclass
class JwtAuth(OpenBaoAuth):
    """Log in with a JWT from an identity provider: an agent's Keycloak service-account token
    (role ``agent-<name>``) or, in Kubernetes, a SPIFFE JWT-SVID."""

    role: str
    jwt: Callable[[], Awaitable[str]]
    mount: str = "jwt"

    async def login(self, http: httpx.AsyncClient, url: str) -> tuple[str, float]:
        return await _login(http, url, self.mount, {"role": self.role, "jwt": await self.jwt()})


def _errors(resp: httpx.Response) -> str:
    try:
        return "; ".join(str(e) for e in resp.json().get("errors", []))[:300] or resp.reason_phrase
    except ValueError:
        return resp.reason_phrase


# ---------------------------------------------------------------- client


class OpenBaoClient:
    """A small async client: logs in lazily, logs in again shortly before the token expires."""

    def __init__(
        self,
        url: str,
        auth: OpenBaoAuth,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.auth = auth
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._token: str | None = None
        self._expires = 0.0

    async def _header(self) -> dict[str, str]:
        if self._token is None or time.monotonic() > self._expires - 30:
            token, ttl = await self.auth.login(self._http, self.url)
            self._token, self._expires = token, time.monotonic() + ttl
        return {"X-Vault-Token": self._token}

    async def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        resp = await self._http.request(
            method, f"{self.url}/v1/{path.lstrip('/')}", json=body, headers=await self._header()
        )
        if resp.status_code == 403:
            raise PermissionError(f"OpenBao refused {method} {path}: {_errors(resp)}")
        if resp.status_code == 404:
            raise KeyError(f"OpenBao has nothing at {path}")
        if resp.status_code >= 400:
            raise OpenBaoError(
                f"OpenBao {method} {path} failed ({resp.status_code}): {_errors(resp)}"
            )
        return resp.json() if resp.content else None

    async def aclose(self) -> None:
        await self._http.aclose()


# ---------------------------------------------------------------- static secrets


class OpenBaoSecretResolver(SecretResolver):
    """``${secret:keycloak/agent-platform#client_secret}`` → KV v2
    ``<mount>/data/keycloak/agent-platform``, field ``client_secret``."""

    def __init__(self, client: OpenBaoClient, mount: str = DEFAULT_KV_MOUNT) -> None:
        self.client = client
        self.mount = mount

    async def resolve(self, reference: str) -> str:
        m = SECRET_REF.match(reference)
        if not m:
            raise KeyError(f"not a secret reference: {reference[:40]!r}")
        try:
            doc = await self.client.request("GET", f"{self.mount}/data/{m['path']}")
        except KeyError:
            raise KeyError(f"secret {m['path']}#{m['key']} not found in OpenBao") from None
        data = (doc or {}).get("data", {}).get("data") or {}
        if m["key"] not in data:
            raise KeyError(f"secret {m['path']} has no field {m['key']}")
        return str(data[m["key"]])

    async def write(self, path: str, values: dict[str, str]) -> None:
        """Used by provisioning, not by the runtime (its policy may only read)."""
        await self.client.request("POST", f"{self.mount}/data/{path}", {"data": values})


# ---------------------------------------------------------------- dynamic credentials


class OpenBaoCredentialBroker(CredentialBroker):
    """Dynamic credentials from ``<mount>/creds/<role>``; leases revoked through
    ``sys/leases/revoke/<lease id>``.

    With ``agent_auth``, each agent gets its own OpenBao client logged in as that agent, so
    OpenBao itself refuses a source the agent's policy does not name (``PermissionError``).
    Without it, all agents share ``auth`` (dev and tests only).
    """

    def __init__(
        self,
        url: str,
        auth: OpenBaoAuth | None = None,
        agent_auth: Callable[[str], OpenBaoAuth] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if auth is None and agent_auth is None:
            raise ValueError("OpenBaoCredentialBroker needs auth or agent_auth")
        self.url = url
        self._auth = auth
        self._agent_auth = agent_auth
        self._transport = transport
        self._clients: dict[str, OpenBaoClient] = {}

    def _client(self, agent_id: str | None) -> OpenBaoClient:
        key = agent_id if self._agent_auth is not None and agent_id else ""
        if key not in self._clients:
            auth = self._agent_auth(key) if key and self._agent_auth else self._auth
            assert auth is not None
            self._clients[key] = OpenBaoClient(self.url, auth, transport=self._transport)
        return self._clients[key]

    @staticmethod
    def _path(source: str) -> str:
        path = source.removeprefix(SOURCE_PREFIX).strip("/")
        mount, _, role = path.partition("/creds/")
        if not mount or not role or "/" in role:
            raise KeyError(f"credential source must look like '<mount>/creds/<role>': {source}")
        return path

    async def issue(self, source: str, ctx: RunContext) -> Credential:
        path = self._path(source)
        try:
            doc = await self._client(ctx.agent_id).request("GET", path)
        except OpenBaoError as err:
            if "unknown role" in str(err):  # OpenBao answers 400 for a role it does not have
                raise KeyError(f"unknown credential source {source}") from None
            raise
        ttl = float(doc.get("lease_duration") or 0)
        if not doc.get("lease_id") or not 0 < ttl <= self.MAX_TTL_SECONDS:
            # a credential without a short lease is refused outright, and revoked if possible
            if doc.get("lease_id"):
                await self._revoke(ctx.agent_id, str(doc["lease_id"]))
            raise PermissionError(
                f"{source} issued a lease of {ttl:.0f}s; tool credentials must expire within "
                f"{self.MAX_TTL_SECONDS}s"
            )
        now = utcnow()
        return Credential(
            lease_id=str(doc["lease_id"]),
            source=f"{SOURCE_PREFIX}{path}",
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl),
            renewable=bool(doc.get("renewable")),
            issued_to=ctx.agent_id if self._agent_auth else None,
            values={k: SecretStr(str(v)) for k, v in (doc.get("data") or {}).items()},
        )

    async def _revoke(self, agent_id: str | None, lease_id: str) -> None:
        with contextlib.suppress(KeyError):  # already gone
            await self._client(agent_id).request("PUT", f"sys/leases/revoke/{lease_id}")

    async def revoke(self, credential: Credential) -> None:
        await self._revoke(credential.issued_to, credential.lease_id)

    async def valid(self, credential: Credential) -> bool:
        try:
            doc = await self._client(credential.issued_to).request(
                "PUT", "sys/leases/lookup", {"lease_id": credential.lease_id}
            )
        except (KeyError, OpenBaoError):
            return False  # OpenBao answers 400 "invalid lease" once a lease is gone
        return float((doc or {}).get("data", {}).get("ttl") or 0) > 0

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()


class VaultCredentialBroker(OpenBaoCredentialBroker):
    """HashiCorp Vault: the same HTTP API. Verified against OpenBao, not against Vault
    itself (ADR 0040)."""
