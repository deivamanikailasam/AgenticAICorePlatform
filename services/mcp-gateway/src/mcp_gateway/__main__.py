"""Run the gateway: ``mcp-gateway`` (or ``python -m mcp_gateway``)."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from agent_contracts.dummies.observability import JsonlAuditSink
from agent_contracts.interfaces import PolicyDecisionPoint
from agent_spec.platform import load_platform
from agent_tools.policy import OpaPolicyDecisionPoint
from agent_tools.policy_cerbos import CerbosPolicyDecisionPoint
from agent_tools.relationships import (
    CompositePolicyDecisionPoint,
    OpenFgaRelationshipChecker,
    load_rules,
)
from agent_tools.secrets import EnvSecretResolver

from mcp_gateway.app import Gateway
from mcp_gateway.settings import Settings


def build_pdp(settings: Settings) -> PolicyDecisionPoint:
    """``opa`` | ``cerbos``, optionally ``+openfga`` for relationship checks."""
    engine, _, extra = settings.pdp.partition("+")
    primary: PolicyDecisionPoint
    if engine == "opa":
        primary = OpaPolicyDecisionPoint(settings.opa_url)
    elif engine == "cerbos":
        primary = CerbosPolicyDecisionPoint(settings.cerbos_url)
    else:
        raise SystemExit(f"MCP_GATEWAY_PDP={settings.pdp!r}: expected opa or cerbos[+openfga]")
    if not extra:
        return primary
    if extra != "openfga":
        raise SystemExit(f"MCP_GATEWAY_PDP={settings.pdp!r}: only +openfga can be added")
    checker = OpenFgaRelationshipChecker(
        settings.openfga_url,
        settings.openfga_store,
        api_key=settings.openfga_key,
        secrets=EnvSecretResolver(),
    )
    return CompositePolicyDecisionPoint(primary, checker, load_rules(Path(settings.openfga_rules)))


def main() -> None:
    settings = Settings()
    # Audit goes to a local hash-chained JSONL file until Step 31 adds the Project 0 audit adapter.
    audit = JsonlAuditSink(os.environ.get("MCP_GATEWAY_AUDIT_FILE", "/tmp/mcp-gateway-audit.jsonl"))  # noqa: S108
    gateway = Gateway(settings, load_platform(), build_pdp(settings), audit)
    uvicorn.run(gateway.app(), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
