"""The reference app's workflow functions."""

import pytest
from invoice_triage.functions import payment_details, validate

GOOD = {
    "id": "INV-1",
    "subtotal": 480,
    "gst": 48,
    "total": 528,
    "currency": "AUD",
    "po_number": "PO-1",
}


def test_valid_invoice_passes_with_its_total() -> None:
    assert validate({"input": {"invoice": GOOD}}) == {
        "invoice_id": "INV-1",
        "amount_aud": 528.0,
        "currency": "AUD",
    }


def test_every_problem_is_reported() -> None:
    bad = {**GOOD, "gst": 50, "total": 530, "po_number": "", "currency": "USD"}
    with pytest.raises(ValueError, match="invoice INV-1") as err:
        validate({"input": {"invoice": bad}})
    message = str(err.value)
    for needle in ("not AUD", "GST 50.00 is not 10% of 480.00", "no purchase order number"):
        assert needle in message


def test_rounding_to_cents_is_exact() -> None:
    odd = {**GOOD, "subtotal": 99.99, "gst": 10.00, "total": 109.99}
    assert validate({"input": {"invoice": odd}})["amount_aud"] == 109.99


def test_payment_details_prefer_the_agents_resolution() -> None:
    state = {
        "validate": {"error": "bad GST"},
        "resolve_exceptions.loop": {"answer": {"invoice_id": "INV-2", "amount": 527.999}},
    }
    assert payment_details(state) == {"invoice_id": "INV-2", "amount_aud": 528.0, "currency": "AUD"}
    with pytest.raises(ValueError, match="not resolved"):
        payment_details({"validate": {"error": "bad GST"}})
