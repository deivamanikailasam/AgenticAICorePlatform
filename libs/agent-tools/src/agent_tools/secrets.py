"""Resolve ``${secret:<path>#<key>}`` references at call time.

``default_resolver()`` is what services use (Step 29): OpenBao (``agent_tools.openbao``) when the
runtime's AppRole is configured; environment variables only in dev and CI, as a fallback after
OpenBao. In staging and prod there is no fallback: a process without OpenBao refuses to start.
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping

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


class ChainedSecretResolver(SecretResolver):
    """Tries each resolver in order; the first that has the secret wins (dev and CI only)."""

    def __init__(self, *resolvers: SecretResolver) -> None:
        self.resolvers = resolvers

    async def resolve(self, reference: str) -> str:
        misses: list[str] = []
        for resolver in self.resolvers:
            try:
                return await resolver.resolve(reference)
            except KeyError as err:
                misses.append(str(err))
        raise KeyError("; ".join(misses) or f"no resolver for {reference[:40]!r}")


DEV_STAGES = frozenset({"dev", "ci", "test"})
_default: SecretResolver | None = None


def default_resolver(env: Mapping[str, str] | None = None) -> SecretResolver:
    """The process's resolver, built once from ``P0_OPENBAO_URL``, ``AGENT_ENV`` and the
    runtime's OpenBao login: ``AGENT_BAO_K8S_ROLE`` (Kubernetes auth with the pod's projected
    service-account token, Step 38) or ``AGENT_BAO_ROLE_ID`` and ``AGENT_BAO_SECRET_ID``
    (AppRole, outside Kubernetes)."""
    global _default
    if env is None and _default is not None:
        return _default
    e = os.environ if env is None else env
    stage = e.get("AGENT_ENV", "dev")
    url, role_id, secret_id = (
        e.get("P0_OPENBAO_URL"),
        e.get("AGENT_BAO_ROLE_ID"),
        e.get("AGENT_BAO_SECRET_ID"),
    )
    k8s_role = e.get("AGENT_BAO_K8S_ROLE")
    resolver: SecretResolver
    if url and (k8s_role or (role_id and secret_id)):
        from agent_tools.openbao import (
            K8S_TOKEN_FILE,
            AppRoleAuth,
            KubernetesAuth,
            OpenBaoAuth,
            OpenBaoClient,
            OpenBaoSecretResolver,
        )

        auth: OpenBaoAuth = (
            KubernetesAuth(k8s_role, e.get("AGENT_BAO_K8S_TOKEN_FILE", K8S_TOKEN_FILE))
            if k8s_role
            else AppRoleAuth(str(role_id), str(secret_id))
        )
        bao = OpenBaoSecretResolver(
            OpenBaoClient(url, auth),
            mount=e.get("AGENT_BAO_KV_MOUNT", "agent-platform"),
        )
        resolver = (
            bao if stage not in DEV_STAGES else ChainedSecretResolver(bao, EnvSecretResolver())
        )
    elif stage in DEV_STAGES:
        resolver = EnvSecretResolver()
    else:
        raise RuntimeError(
            f"AGENT_ENV={stage}: secrets come only from OpenBao; set P0_OPENBAO_URL and "
            "AGENT_BAO_K8S_ROLE (Kubernetes) or AGENT_BAO_ROLE_ID and AGENT_BAO_SECRET_ID"
        )
    if env is None:
        _default = resolver
    return resolver
