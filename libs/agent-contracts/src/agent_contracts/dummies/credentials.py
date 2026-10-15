"""Credential brokers for tests (Step 29): generated credentials, and fixed test values.

Both keep leases in memory, expire them after ``ttl_seconds`` and honour ``revoke``. Neither
talks to a secret manager; ``agent_tools.openbao`` is the real one.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from datetime import timedelta

from pydantic import SecretStr

from agent_contracts.dummies._util import mark_dummies
from agent_contracts.interfaces import CredentialBroker
from agent_contracts.models import Credential, RunContext, new_id, utcnow


class InMemoryCredentialBroker(CredentialBroker):
    """Issues a fresh random user name and password for each allowed source."""

    def __init__(
        self,
        sources: Sequence[str] = ("db/tool-reader",),
        forbidden: Sequence[str] = ("db/admin",),
        ttl_seconds: int = 300,
    ) -> None:
        self.sources = set(sources)
        self.forbidden = set(forbidden)
        self.ttl_seconds = min(ttl_seconds, self.MAX_TTL_SECONDS)
        self._leases: dict[str, Credential] = {}

    def _values(self, source: str) -> dict[str, SecretStr]:
        return {
            "username": SecretStr(f"v-{source.rsplit('/', 1)[-1]}-{secrets.token_hex(4)}"),
            "password": SecretStr(secrets.token_urlsafe(18)),
        }

    async def issue(self, source: str, ctx: RunContext) -> Credential:
        if source in self.forbidden:
            raise PermissionError(f"agent {ctx.agent_id} may not use credential source {source}")
        if source not in self.sources:
            raise KeyError(f"unknown credential source {source}")
        now = utcnow()
        cred = Credential(
            lease_id=f"{source}/{new_id()}",
            source=source,
            issued_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
            issued_to=ctx.agent_id,
            values=self._values(source),
        )
        self._leases[cred.lease_id] = cred
        return cred

    async def revoke(self, credential: Credential) -> None:
        self._leases.pop(credential.lease_id, None)

    async def valid(self, credential: Credential) -> bool:
        held = self._leases.get(credential.lease_id)
        return held is not None and held.ttl_seconds() > 0


class StaticCredentialBroker(InMemoryCredentialBroker):
    """Issues the same test values every time (for tests that check exact values)."""

    def __init__(
        self,
        values: Mapping[str, Mapping[str, str]] | None = None,
        ttl_seconds: int = 60,
    ) -> None:
        self.fixed = dict(values or {"db/tool-reader": {"token": "test-token-not-a-secret"}})
        super().__init__(
            sources=tuple(self.fixed), forbidden=("db/admin",), ttl_seconds=ttl_seconds
        )

    def _values(self, source: str) -> dict[str, SecretStr]:
        return {k: SecretStr(v) for k, v in self.fixed[source].items()}


mark_dummies(globals())
