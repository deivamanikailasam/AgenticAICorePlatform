"""A demo ERP as an MCP server (stdio) for the reference app: ``get_invoice`` and
``post_payment``. Data lives in a JSON file (``ERP_DATA``, seeded on first use) so a test or a
demo can inspect payments afterwards.

The ERP enforces its own rules too (defence in depth): it refuses payments for unknown
invoices, in another currency, above the invoice total, or for an invoice already paid.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

SEED: dict[str, Any] = {
    "invoices": {
        "INV-1001": {
            "id": "INV-1001",
            "supplier": "Acme Office Supplies Pty Ltd",
            "subtotal": 480.0,
            "gst": 48.0,
            "total": 528.0,
            "currency": "AUD",
            "po_number": "PO-7781",
        },
        "INV-1002": {
            "id": "INV-1002",
            "supplier": "Harbour Catering",
            "subtotal": 480.0,
            "gst": 50.0,
            "total": 530.0,
            "currency": "AUD",
            "po_number": "",
            "notes": "PO approved by email: PO-7790",
        },
    },
    "payments": [],
}

server = MCPServer(name="erp-demo", version="0.3.0")


def _path() -> Path:
    path = Path(os.environ.get("ERP_DATA") or Path.home() / ".agent-platform" / "erp-demo.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        path.write_text(json.dumps(SEED, indent=2))
    data: dict[str, Any] = json.loads(path.read_text())
    return data


@server.tool(
    description="Look up an invoice by id.",
    annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
)
def get_invoice(invoice_id: str) -> dict[str, Any]:
    invoice = _load()["invoices"].get(invoice_id)
    if invoice is None:
        raise ValueError(f"no invoice {invoice_id}")
    return dict(invoice)


@server.tool(
    description="Pay an invoice. Irreversible.",
    annotations=ToolAnnotations(destructive_hint=True),
)
def post_payment(invoice_id: str, amount_aud: float, currency: str = "AUD") -> dict[str, Any]:
    data = _load()
    invoice = data["invoices"].get(invoice_id)
    if invoice is None:
        raise ValueError(f"no invoice {invoice_id}")
    if currency != invoice["currency"]:
        raise ValueError(f"currency {currency} does not match the invoice ({invoice['currency']})")
    if not 0 < amount_aud <= invoice["total"]:
        raise ValueError(f"amount {amount_aud} must be above 0 and at most {invoice['total']}")
    if any(p["invoice_id"] == invoice_id for p in data["payments"]):
        raise ValueError(f"invoice {invoice_id} is already paid")
    payment = {
        "id": f"PAY-{uuid.uuid4().hex[:8]}",
        "invoice_id": invoice_id,
        "amount_aud": amount_aud,
    }
    data["payments"].append(payment)
    _path().write_text(json.dumps(data, indent=2))
    return payment


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
