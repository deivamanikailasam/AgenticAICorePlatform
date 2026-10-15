"""Run the gateway: ``mcp-gateway`` (or ``python -m mcp_gateway``)."""

from __future__ import annotations

import os

import uvicorn
from agent_contracts.dummies.observability import JsonlAuditSink
from agent_contracts.interfaces import PolicyDecisionPoint
from agent_spec.platform import load_platform
from agent_tools.policy import OpaPolicyDecisionPoint
from agent_tools.policy_cerbos import CerbosPolicyDecisionPoint

from mcp_gateway.app import Gateway
from mcp_gateway.settings import Settings


def build_pdp(settings: Settings) -> PolicyDecisionPoint:
    if settings.pdp == "opa":
        return OpaPolicyDecisionPoint(settings.opa_url)
    if settings.pdp == "cerbos":
        return CerbosPolicyDecisionPoint(settings.cerbos_url)
    raise SystemExit(f"MCP_GATEWAY_PDP={settings.pdp!r}: expected opa or cerbos")


def main() -> None:
    settings = Settings()
    # Audit goes to a local hash-chained JSONL file until Step 31 adds the Project 0 audit adapter.
    audit = JsonlAuditSink(os.environ.get("MCP_GATEWAY_AUDIT_FILE", "/tmp/mcp-gateway-audit.jsonl"))  # noqa: S108
    gateway = Gateway(settings, load_platform(), build_pdp(settings), audit)
    uvicorn.run(gateway.app(), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
