"""Choose the policy decision point from configuration (shared by the MCP gateway and the run API).

``opa`` (default) | ``cerbos``, optionally ``+openfga`` for relationship checks (Step 10).
"""

from __future__ import annotations

from pathlib import Path

from agent_contracts.interfaces import PolicyDecisionPoint

from agent_tools.policy import OpaPolicyDecisionPoint
from agent_tools.policy_cerbos import CerbosPolicyDecisionPoint
from agent_tools.relationships import (
    CompositePolicyDecisionPoint,
    OpenFgaRelationshipChecker,
    load_rules,
)
from agent_tools.secrets import EnvSecretResolver


def build_pdp(
    choice: str,
    *,
    opa_url: str,
    cerbos_url: str = "http://127.0.0.1:3592",
    openfga_url: str = "http://127.0.0.1:8085",
    openfga_store: str = "",
    openfga_key: str | None = None,
    openfga_rules: str = "policies/openfga/relations.yaml",
) -> PolicyDecisionPoint:
    engine, _, extra = choice.partition("+")
    primary: PolicyDecisionPoint
    if engine == "opa":
        primary = OpaPolicyDecisionPoint(opa_url)
    elif engine == "cerbos":
        primary = CerbosPolicyDecisionPoint(cerbos_url)
    else:
        raise ValueError(f"policy engine {choice!r}: expected opa or cerbos[+openfga]")
    if not extra:
        return primary
    if extra != "openfga":
        raise ValueError(f"policy engine {choice!r}: only +openfga can be added")
    checker = OpenFgaRelationshipChecker(
        openfga_url, openfga_store, api_key=openfga_key, secrets=EnvSecretResolver()
    )
    return CompositePolicyDecisionPoint(primary, checker, load_rules(Path(openfga_rules)))
