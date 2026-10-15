"""Resolve ``${secret:<path>#<key>}`` references at call time. OpenBao arrives in Step 29."""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod

SECRET_REF = re.compile(r"^\$\{secret:(?P<path>[A-Za-z0-9_./-]+)#(?P<key>[A-Za-z0-9_.-]+)\}$")


class SecretResolver(ABC):
    @abstractmethod
    async def resolve(self, reference: str) -> str:
        """Return the secret value for a ``${secret:path#key}`` reference; raise ``KeyError`` if absent."""


class EnvSecretResolver(SecretResolver):
    """Dev/CI resolver: ``${secret:keycloak/agent-platform#client_secret}`` →
    env ``SECRET__KEYCLOAK__AGENT_PLATFORM__CLIENT_SECRET``. Never used in production."""

    async def resolve(self, reference: str) -> str:
        m = SECRET_REF.match(reference)
        if not m:
            raise KeyError(f"not a secret reference: {reference[:40]!r}")
        name = "SECRET__" + re.sub(
            r"[^A-Z0-9_]+", "_", f"{m['path']}__{m['key']}".upper().replace("/", "__")
        )
        value = os.environ.get(name)
        if value is None:
            raise KeyError(f"secret {m['path']}#{m['key']} not available (env {name})")
        return value


async def maybe_secret(value: str | None, resolver: SecretResolver) -> str | None:
    """Resolve ``value`` if it is a secret reference; return plain values unchanged."""
    if value is not None and SECRET_REF.match(value):
        return await resolver.resolve(value)
    return value
