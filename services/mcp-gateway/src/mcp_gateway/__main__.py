"""Run the gateway: ``mcp-gateway`` (or ``python -m mcp_gateway``)."""

from __future__ import annotations

import os

import uvicorn
from agent_contracts.dummies.observability import JsonlAuditSink
from agent_spec.platform import load_platform
from agent_tools.policy import OpaPolicyDecisionPoint

from mcp_gateway.app import Gateway
from mcp_gateway.settings import Settings


def main() -> None:
    settings = Settings()
    # Audit goes to a local hash-chained JSONL file until Step 31 adds the Project 0 audit adapter.
    audit = JsonlAuditSink(os.environ.get("MCP_GATEWAY_AUDIT_FILE", "/tmp/mcp-gateway-audit.jsonl"))  # noqa: S108
    gateway = Gateway(settings, load_platform(), OpaPolicyDecisionPoint(settings.opa_url), audit)
    uvicorn.run(gateway.app(), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
