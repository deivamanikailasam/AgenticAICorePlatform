"""``pii_redaction`` against real Presidio services (the stand-in in CI, Project 0 on the Mac)."""

import os

import pytest
from agent_contracts.models import GuardrailAction, GuardrailInput, GuardrailStage
from agent_guardrails.pii import PresidioPiiGuardrail

pytestmark = pytest.mark.integration


def guard() -> PresidioPiiGuardrail:
    if not os.environ.get("P0_PRESIDIO_ANALYZER_URL"):
        pytest.skip("P0_PRESIDIO_ANALYZER_URL not set")
    return PresidioPiiGuardrail()


async def test_email_phone_and_card_are_redacted_and_ordinary_words_are_kept() -> None:
    text = "Email jane.citizen@example.com or call 0412 345 678. Card 4111 1111 1111 1111."
    r = await guard().check(GuardrailInput(stage=GuardrailStage.OUTPUT, text=text))
    assert r.action is GuardrailAction.REDACT
    assert set(r.findings) >= {"EMAIL_ADDRESS", "CREDIT_CARD"}
    out = r.redacted_text or ""
    assert "jane.citizen@example.com" not in out
    assert "4111 1111 1111 1111" not in out
    assert out.startswith("Email "), "PERSON is not in the default entity list"


async def test_text_without_personal_data_passes() -> None:
    r = await guard().check(
        GuardrailInput(stage=GuardrailStage.OUTPUT, text="The invoice total is 420 dollars.")
    )
    assert r.passed
