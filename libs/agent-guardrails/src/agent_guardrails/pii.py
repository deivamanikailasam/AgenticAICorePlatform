"""PII detection and redaction with Presidio (Project 0's analyzer and anonymizer services).

``/analyze`` finds entities with scores; entities at or above ``threshold`` from the configured
list are replaced (``/anonymize``, operator ``replace``) with ``<ENTITY_TYPE>``. The default
entity list leaves out types that are noisy in agent text (PERSON flags ordinary capitalised
words, DATE_TIME and URL flag business content); add them per spec when needed.

``pii_redaction.cloud_dlp`` is a documented stub: a cloud DLP service needs an account and sends
the text to a third party, a data-residency decision for the owner (ADR 0038).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any, ClassVar

import httpx
from agent_contracts.interfaces import Guardrail
from agent_contracts.models import (
    GuardrailAction,
    GuardrailInput,
    GuardrailResult,
    GuardrailStage,
)

DEFAULT_ENTITIES = (
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "IP_ADDRESS",
    "US_SSN",
    "US_PASSPORT",
    "US_BANK_NUMBER",
    "CRYPTO",
)


class PresidioPiiGuardrail(Guardrail):
    name: ClassVar[str] = "pii_redaction"
    stages: ClassVar[frozenset[GuardrailStage]] = frozenset(
        {GuardrailStage.OUTPUT, GuardrailStage.TOOL_RESULT, GuardrailStage.INPUT}
    )
    redacts: ClassVar[bool] = True

    def __init__(
        self,
        analyzer_url: str | None = None,
        anonymizer_url: str | None = None,
        entities: Sequence[str] = DEFAULT_ENTITIES,
        threshold: float = 0.5,
        language: str = "en",
        stages: Sequence[str] = ("output",),
        timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.analyzer = (
            analyzer_url or os.environ.get("P0_PRESIDIO_ANALYZER_URL", "http://127.0.0.1:5002")
        ).rstrip("/")
        self.anonymizer = (
            anonymizer_url or os.environ.get("P0_PRESIDIO_ANONYMIZER_URL", "http://127.0.0.1:5001")
        ).rstrip("/")
        self.entities = list(entities)
        self.threshold, self.language = threshold, language
        self.active = frozenset(GuardrailStage(s) for s in stages)
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def check(self, payload: GuardrailInput) -> GuardrailResult:
        if payload.stage not in self.active or not payload.text.strip():
            return GuardrailResult(guardrail=self.name, passed=True)
        try:
            found = await self._client.post(
                f"{self.analyzer}/analyze",
                json={"text": payload.text, "language": self.language, "entities": self.entities},
            )
            found.raise_for_status()
            hits: list[dict[str, Any]] = [
                h for h in found.json() if float(h.get("score", 0)) >= self.threshold
            ]
            if not hits:
                return GuardrailResult(guardrail=self.name, passed=True)
            done = await self._client.post(
                f"{self.anonymizer}/anonymize",
                json={
                    "text": payload.text,
                    "analyzer_results": hits,
                    "anonymizers": {"DEFAULT": {"type": "replace"}},
                },
            )
            done.raise_for_status()
            redacted = str(done.json()["text"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            # fail closed: text that may hold personal data does not go out unchecked
            return GuardrailResult(
                guardrail=self.name,
                passed=False,
                action=GuardrailAction.BLOCK,
                findings=(f"PII check unavailable ({type(exc).__name__}); blocked",),
            )
        return GuardrailResult(
            guardrail=self.name,
            passed=False,
            action=GuardrailAction.REDACT,
            findings=tuple(sorted({str(h["entity_type"]) for h in hits})),
            redacted_text=redacted,
        )


class CloudDlpPiiGuardrail(Guardrail):
    """Documented stub (ADR 0038): needs a cloud account and a data-residency decision."""

    name: ClassVar[str] = "pii_redaction.cloud_dlp"
    stages: ClassVar[frozenset[GuardrailStage]] = frozenset({GuardrailStage.OUTPUT})
    redacts: ClassVar[bool] = True

    def __init__(self, **_: Any) -> None:
        raise NotImplementedError(
            "pii_redaction.cloud_dlp is a stub: a cloud DLP service sends text to a third party "
            "and needs an account; use pii_redaction (Presidio, Project 0) — see ADR 0038"
        )

    async def check(self, payload: GuardrailInput) -> GuardrailResult:  # pragma: no cover
        raise NotImplementedError
