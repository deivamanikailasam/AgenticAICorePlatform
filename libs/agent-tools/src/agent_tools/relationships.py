"""Relationship checks with OpenFGA, layered on a policy decision point (Step 10).

Attribute rules (OPA or Cerbos) answer "may an authenticated finance user of tenant acme pay
invoices at all?"; relationships answer "is *this* user a payer of *this* invoice?". The
``CompositePolicyDecisionPoint`` asks the primary PDP first, then, for tools listed in
``policies/openfga/relations.yaml``, checks the relation in OpenFGA. A missing relationship, a
missing object argument or an OpenFGA failure turns the answer into ``deny``; a relationship never
loosens the primary's answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from agent_contracts.interfaces import PolicyDecisionPoint
from agent_contracts.models import PolicyDecision, PolicyEffect, PolicyInput

from agent_tools.secrets import EnvSecretResolver, SecretResolver, maybe_secret


class RelationshipCheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class RelationshipRule:
    object_type: str
    argument: str
    relation: str


def load_rules(path: Path) -> dict[str, RelationshipRule]:
    doc = yaml.safe_load(path.read_text()) or {}
    return {tool: RelationshipRule(**rule) for tool, rule in (doc.get("rules") or {}).items()}


class OpenFgaRelationshipChecker:
    """``POST /stores/{store}/check`` against an OpenFGA server with a pre-shared key."""

    def __init__(
        self,
        url: str,
        store: str,
        api_key: str | None = None,
        secrets: SecretResolver | None = None,
        model_id: str | None = None,
        timeout: float = 2.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.store = store  # a store id, or a store name resolved on first use
        self.model_id = model_id
        self._key_ref = api_key
        self._secrets = secrets or EnvSecretResolver()
        self._store_id: str | None = None
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def _headers(self) -> dict[str, str]:
        key = await maybe_secret(self._key_ref, self._secrets) if self._key_ref else None
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def store_id(self) -> str:
        if self._store_id is None:
            resp = await self._client.get(
                "/stores", params={"name": self.store}, headers=await self._headers()
            )
            resp.raise_for_status()
            ids = [s["id"] for s in resp.json().get("stores", []) if s.get("name") == self.store]
            if len(ids) == 1:
                self._store_id = ids[0]
            elif not ids:
                self._store_id = self.store  # not a name: treat it as an id
            else:
                raise RelationshipCheckError(f"several OpenFGA stores are named {self.store!r}")
        return self._store_id

    async def check(self, user: str, relation: str, obj: str) -> bool:
        body: dict[str, Any] = {"tuple_key": {"user": user, "relation": relation, "object": obj}}
        if self.model_id:
            body["authorization_model_id"] = self.model_id
        resp = await self._client.post(
            f"/stores/{await self.store_id()}/check", json=body, headers=await self._headers()
        )
        resp.raise_for_status()
        return resp.json().get("allowed") is True

    async def write(self, tuples: list[tuple[str, str, str]]) -> None:
        """Write (user, relation, object) tuples. For provisioning and tests; apps own their data."""
        body = {
            "writes": {
                "tuple_keys": [{"user": u, "relation": r, "object": o} for u, r, o in tuples]
            }
        }
        resp = await self._client.post(
            f"/stores/{await self.store_id()}/write", json=body, headers=await self._headers()
        )
        resp.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()


class CompositePolicyDecisionPoint(PolicyDecisionPoint):
    """``policy_decision_point.composite``: primary PDP, then OpenFGA relationships."""

    def __init__(
        self,
        primary: PolicyDecisionPoint,
        checker: OpenFgaRelationshipChecker,
        rules: Mapping[str, RelationshipRule],
    ) -> None:
        self.primary = primary
        self.checker = checker
        self.rules = dict(rules)

    async def decide(self, request: PolicyInput) -> PolicyDecision:
        decision = await self.primary.decide(request)
        rule = self.rules.get(request.tool or "")
        if decision.effect is PolicyEffect.DENY or rule is None:
            return decision
        version = f"{decision.policy_version}+openfga"
        object_id = request.arguments.get(rule.argument)
        user = request.subject.get("user")
        if object_id is None or not user:
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                reasons=(f"relationship check needs the user and argument {rule.argument}",),
                policy_version=version,
            )
        obj = f"{rule.object_type}:{request.ctx.tenant_id}/{object_id}"
        try:
            allowed = await self.checker.check(f"user:{user}", rule.relation, obj)
        except (httpx.HTTPError, ValueError, RelationshipCheckError) as exc:
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                reasons=(f"relationship engine unavailable: {type(exc).__name__}",),
                policy_version=version,
            )
        if not allowed:
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                reasons=(f"user {user} is not {rule.relation} of {obj}",),
                policy_version=version,
            )
        return decision.model_copy(
            update={
                "reasons": (*decision.reasons, f"{user} is {rule.relation} of {obj}"),
                "policy_version": version,
            }
        )
