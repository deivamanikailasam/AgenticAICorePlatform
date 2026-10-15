"""Telemetry for the platform's services (Step 30).

Spans go to Project 0's OpenTelemetry Collector (``P0_OTEL_OTLP_ENDPOINT``) without content.
With ``LANGFUSE_HOST`` and ``LANGFUSE_PUBLIC_KEY`` set, they also go to Langfuse with prompt
and response content, redacted first: secret shapes always, and personal data through Presidio
when ``P0_PRESIDIO_ANALYZER_URL`` is set. The Langfuse secret key comes from the secret store
(``LANGFUSE_SECRET_KEY`` is a reference, by default ``${secret:langfuse/agent-platform#secret_key}``).
"""

from __future__ import annotations

import os

from agent_contracts.interfaces import Guardrail
from agent_contracts.models import GuardrailAction, GuardrailInput, GuardrailStage, Trust
from agent_guardrails.pii import PresidioPiiGuardrail
from agent_guardrails.secrets import SecretLeakGuardrail
from agent_telemetry.content import Redactor
from agent_telemetry.export import LangfuseTarget, setup
from agent_tools.secrets import SecretResolver, maybe_secret
from opentelemetry.sdk.trace import TracerProvider


def content_redactor(pii: Guardrail | None = None) -> Redactor:
    """Secrets, then PII (``pii``: Presidio). ``None`` (content withheld) when the PII check is
    unavailable."""
    secret = SecretLeakGuardrail()

    async def redact(text: str) -> str | None:
        found = await secret.check(
            GuardrailInput(stage=GuardrailStage.OUTPUT, text=text, trust=Trust.TRUSTED)
        )
        if found.action is GuardrailAction.REDACT and found.redacted_text is not None:
            text = found.redacted_text
        if pii is not None:
            found = await pii.check(
                GuardrailInput(stage=GuardrailStage.OUTPUT, text=text, trust=Trust.TRUSTED)
            )
            if found.action is GuardrailAction.BLOCK:
                return None
            if found.action is GuardrailAction.REDACT and found.redacted_text is not None:
                text = found.redacted_text
        return text

    return redact


async def setup_from_env(service: str, secrets: SecretResolver) -> TracerProvider:
    host, public = os.environ.get("LANGFUSE_HOST"), os.environ.get("LANGFUSE_PUBLIC_KEY")
    langfuse = None
    if host and public:
        secret_ref = os.environ.get(
            "LANGFUSE_SECRET_KEY", "${secret:langfuse/agent-platform#secret_key}"
        )
        secret = await maybe_secret(secret_ref, secrets)
        langfuse = LangfuseTarget(host, public, secret or "")
    return setup(
        service,
        os.environ.get("P0_OTEL_OTLP_ENDPOINT", "http://127.0.0.1:4318"),
        langfuse,
        content_redactor(
            PresidioPiiGuardrail(stages=("output",))
            if os.environ.get("P0_PRESIDIO_ANALYZER_URL")
            else None
        )
        if langfuse
        else None,
    )
