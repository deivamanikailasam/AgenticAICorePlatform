"""Workflow functions for ``agents/invoice-triage`` (registered as ``invoice.*``).

``validate`` is the deterministic gate: an invoice that passes goes straight to payment; one
that fails raises, so the ``on_error`` edge hands it to the exceptions agent with the reason.
``payment_details`` picks what to pay: the agent's resolution when there was one, otherwise the
validated invoice.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

GST_RATE = Decimal("0.10")
CENT = Decimal("0.01")


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(CENT)


def validate(state: dict[str, Any]) -> dict[str, Any]:
    invoice = dict(state["input"]["invoice"])
    problems = []
    subtotal, gst, total = (_money(invoice.get(k, 0)) for k in ("subtotal", "gst", "total"))
    if invoice.get("currency") != "AUD":
        problems.append(f"currency {invoice.get('currency')!r} is not AUD")
    if subtotal <= 0:
        problems.append("subtotal must be positive")
    if gst != (subtotal * GST_RATE).quantize(CENT):
        problems.append(f"GST {gst} is not 10% of {subtotal}")
    if total != subtotal + gst:
        problems.append(f"total {total} is not subtotal plus GST ({subtotal + gst})")
    if not invoice.get("po_number"):
        problems.append("no purchase order number")
    if problems:
        raise ValueError(f"invoice {invoice.get('id')}: " + "; ".join(problems))
    return {"invoice_id": invoice["id"], "amount_aud": float(total), "currency": "AUD"}


def payment_details(state: dict[str, Any]) -> dict[str, Any]:
    # the agent node's result is stored under its loop node (resolve_exceptions.loop)
    resolved = state.get("resolve_exceptions.loop") or state.get("resolve_exceptions")
    if isinstance(resolved, dict) and isinstance(resolved.get("answer"), dict):
        answer = resolved["answer"]
        return {
            "invoice_id": str(answer["invoice_id"]),
            "amount_aud": float(_money(answer["amount"])),
            "currency": str(answer.get("currency", "AUD")),
        }
    checked = state["validate"]
    if "error" in checked:
        raise ValueError("the invoice failed validation and was not resolved")
    return {k: checked[k] for k in ("invoice_id", "amount_aud", "currency")}
