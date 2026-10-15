"""Run the gateway: ``mcp-gateway`` (or ``python -m mcp_gateway``)."""

from __future__ import annotations

import os

import uvicorn
from agent_audit.factory import audit_sink_from_env
from agent_contracts.interfaces import PolicyDecisionPoint
from agent_spec.platform import load_platform
from agent_tools import pdp_factory

from mcp_gateway.app import Gateway
from mcp_gateway.settings import Settings


def build_pdp(settings: Settings) -> PolicyDecisionPoint:
    """``opa`` | ``cerbos``, optionally ``+openfga`` for relationship checks."""
    try:
        return pdp_factory.build_pdp(
            settings.pdp,
            opa_url=settings.opa_url,
            cerbos_url=settings.cerbos_url,
            openfga_url=settings.openfga_url,
            openfga_store=settings.openfga_store,
            openfga_key=settings.openfga_key,
            openfga_rules=settings.openfga_rules,
        )
    except ValueError as exc:
        raise SystemExit(f"MCP_GATEWAY_PDP: {exc}") from None


def main() -> None:
    settings = Settings()
    # Step 31: the shared hash-chained Postgres trail (AGENT_AUDIT_DSN), else a local file (dev)
    local = os.environ.get("MCP_GATEWAY_AUDIT_FILE", "/tmp/mcp-gateway-audit.jsonl")  # noqa: S108
    audit = audit_sink_from_env(local)
    gateway = Gateway(settings, load_platform(), build_pdp(settings), audit)
    uvicorn.run(gateway.app(), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
