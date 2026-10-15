"""``policy_decision_point.opa``: ask Project 0's OPA to decide every tool call (ADR 0009).

Fails closed: if OPA is unreachable, returns an error, or answers with something that is not a
valid decision, the effect is ``deny``.
"""

from __future__ import annotations

import httpx
from agent_contracts.interfaces import PolicyDecisionPoint
from agent_contracts.models import PolicyDecision, PolicyEffect, PolicyInput


class OpaPolicyDecisionPoint(PolicyDecisionPoint):
    """Evaluates ``data.<package path>`` (default ``agent/tools/decision``) over HTTP."""

    def __init__(
        self,
        url: str = "http://127.0.0.1:8181",
        path: str = "agent/tools/decision",
        timeout: float = 2.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.path = path.strip("/")
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def decide(self, request: PolicyInput) -> PolicyDecision:
        body = {"input": request.model_dump(mode="json")}
        try:
            resp = await self._client.post(f"/v1/data/{self.path}", json=body)
            resp.raise_for_status()
            result = resp.json().get("result")
            if not isinstance(result, dict):
                return PolicyDecision(
                    effect=PolicyEffect.DENY, reasons=(f"policy {self.path} is not loaded",)
                )
            return PolicyDecision(
                effect=PolicyEffect(result.get("effect", "deny")),
                reasons=tuple(str(r) for r in result.get("reasons", ())),
                approvers=result.get("approvers"),
                policy_version=result.get("version"),
            )
        except (httpx.HTTPError, ValueError) as exc:
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                reasons=(f"policy engine unavailable: {type(exc).__name__}",),
            )

    async def aclose(self) -> None:
        await self._client.aclose()
