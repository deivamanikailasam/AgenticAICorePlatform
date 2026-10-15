"""Run the API: ``agent-api`` (or ``python -m agent_api``)."""

from __future__ import annotations

import uvicorn
from agent_contracts.dummies.observability import JsonlAuditSink
from agent_spec.platform import load_platform
from agent_tools.auth import KeycloakTokenVerifier
from agent_tools.identity import TokenExchanger
from agent_tools.pdp_factory import build_pdp
from agent_tools.secrets import EnvSecretResolver

from agent_api.app import build_app
from agent_api.assembly import Assembly
from agent_api.runs import RunManager
from agent_api.settings import Settings


def main() -> None:
    settings = Settings()
    # Audit goes to a local hash-chained JSONL file until Step 31 adds the Project 0 audit adapter.
    audit = JsonlAuditSink(settings.audit_file)
    exchanger = TokenExchanger(
        settings.token_url, settings.client_id, settings.client_secret, EnvSecretResolver()
    )
    assembly = Assembly(
        load_platform(),
        build_pdp(settings.pdp, opa_url=settings.opa_url, cerbos_url=settings.cerbos_url),
        audit,
        rag_core_url=settings.rag_core_url,
        exchanger=exchanger,
    )
    manager = RunManager(RunManager.load_agents(settings.agents_dir), assembly)
    verifier = KeycloakTokenVerifier(settings.issuer, audience=settings.audience)
    uvicorn.run(
        build_app(manager, verifier), host=settings.host, port=settings.port, log_level="info"
    )


if __name__ == "__main__":
    main()
