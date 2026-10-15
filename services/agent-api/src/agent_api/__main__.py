"""Run the API: ``agent-api`` (or ``python -m agent_api``)."""

from __future__ import annotations

import asyncio

import uvicorn
from agent_contracts.dummies.observability import JsonlAuditSink
from agent_contracts.interfaces import Checkpointer
from agent_runtime.checkpoints import FieldCipher, PostgresCheckpointer
from agent_runtime.models.ledger import PostgresUsageLedger
from agent_spec.platform import load_platform
from agent_tools.auth import KeycloakTokenVerifier
from agent_tools.identity import TokenExchanger
from agent_tools.pdp_factory import build_pdp
from agent_tools.secrets import EnvSecretResolver

from agent_api.app import build_app
from agent_api.assembly import Assembly
from agent_api.registry import registry_gate
from agent_api.runs import RunManager
from agent_api.settings import Settings
from agent_api.store import PostgresRunStore, RunStore


def durability(settings: Settings) -> tuple[Checkpointer | None, RunStore | None]:
    """``AGENT_API_STORE=postgres``: runs and encrypted checkpoints in the agent database."""
    if settings.store == "memory":
        return None, None
    if settings.store != "postgres" or not settings.db_dsn:
        raise SystemExit("AGENT_API_STORE=postgres needs AGENT_DB_DSN")
    key = asyncio.run(EnvSecretResolver().resolve(settings.checkpoint_key))
    cipher = FieldCipher.from_b64(key)
    return PostgresCheckpointer(settings.db_dsn, cipher), PostgresRunStore(settings.db_dsn, cipher)


def main() -> None:
    settings = Settings()
    checkpointer, store = durability(settings)
    # Audit goes to a local hash-chained JSONL file until Step 31 adds the Project 0 audit adapter.
    audit = JsonlAuditSink(settings.audit_file)
    exchanger = TokenExchanger(
        settings.token_url, settings.client_id, settings.client_secret, EnvSecretResolver()
    )
    # Step 25: every deployed spec is in the registry (new versions as draft); in staging and
    # prod only approved versions run or are delegated to
    agents = RunManager.load_agents(settings.agents_dir)
    gate = asyncio.run(registry_gate(settings.environment, audit, agents))
    assembly = Assembly(
        load_platform(),
        build_pdp(settings.pdp, opa_url=settings.opa_url, cerbos_url=settings.cerbos_url),
        audit,
        rag_core_url=settings.rag_core_url,
        exchanger=exchanger,
        checkpointer=checkpointer,
        admit=gate.refusal,
        ledger=PostgresUsageLedger(settings.db_dsn) if settings.db_dsn else None,
    )
    manager = RunManager(agents, assembly, store)
    verifier = KeycloakTokenVerifier(
        settings.issuer, audience=settings.audience, jwks_url=settings.jwks_url
    )
    uvicorn.run(
        build_app(manager, verifier), host=settings.host, port=settings.port, log_level="info"
    )


if __name__ == "__main__":
    main()
