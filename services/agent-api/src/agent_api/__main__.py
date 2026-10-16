"""Run the API: ``agent-api`` (or ``python -m agent_api``)."""

from __future__ import annotations

import asyncio

import uvicorn
from agent_audit.factory import audit_sink_from_env
from agent_contracts.dummies.safety import InMemoryApprovalGate
from agent_contracts.interfaces import Checkpointer
from agent_runtime.approval_store import PostgresApprovalGate
from agent_runtime.checkpoints import FieldCipher, PostgresCheckpointer
from agent_runtime.models.ledger import PostgresUsageLedger
from agent_spec.platform import load_platform
from agent_tools.approvals import OpaApprovalRouter, RulesApprovalRouter
from agent_tools.auth import KeycloakTokenVerifier
from agent_tools.pdp_factory import build_pdp
from agent_tools.secrets import default_resolver, resolve_env_references

from agent_api.app import build_app
from agent_api.assembly import Assembly, load_gateway_keys
from agent_api.desk import ApprovalDesk
from agent_api.identity import identity_from_env
from agent_api.registry import registry_gate
from agent_api.runs import RunManager
from agent_api.settings import Settings
from agent_api.store import PostgresRunStore, RunStore
from agent_api.telemetry import setup_from_env


def durability(settings: Settings) -> tuple[Checkpointer | None, RunStore | None]:
    """``AGENT_API_STORE=postgres``: runs and encrypted checkpoints in the agent database."""
    if settings.store == "memory":
        return None, None
    if settings.store != "postgres" or not settings.db_dsn:
        raise SystemExit("AGENT_API_STORE=postgres needs AGENT_DB_DSN")
    key = asyncio.run(default_resolver().resolve(settings.checkpoint_key))
    cipher = FieldCipher.from_b64(key)
    return PostgresCheckpointer(settings.db_dsn, cipher), PostgresRunStore(settings.db_dsn, cipher)


def main() -> None:
    # Step 38: database connection strings may be OpenBao references (Kubernetes)
    resolve_env_references()
    settings = Settings()
    checkpointer, store = durability(settings)
    # Step 31: the shared hash-chained Postgres trail (AGENT_AUDIT_DSN), else a local file (dev)
    audit = audit_sink_from_env(settings.audit_file)
    # Step 29: per-agent identities, secrets from OpenBao and call-time tool credentials
    identity = identity_from_env(settings)
    # Step 30: GenAI spans to the collector (no content); to Langfuse with redacted content
    asyncio.run(setup_from_env("agent-api", identity.secrets))
    platform = load_platform()
    gateway_keys = asyncio.run(load_gateway_keys(platform, identity.secrets, settings.environment))
    # Step 25: every deployed spec is in the registry (new versions as draft); in staging and
    # prod only approved versions run or are delegated to
    agents = RunManager.load_agents(settings.agents_dir)
    gate = asyncio.run(registry_gate(settings.environment, audit, agents))
    assembly = Assembly(
        platform,
        build_pdp(settings.pdp, opa_url=settings.opa_url, cerbos_url=settings.cerbos_url),
        audit,
        rag_core_url=settings.rag_core_url,
        exchanger=identity.exchanger,
        secrets=identity.secrets,
        broker=identity.broker,
        gateway_keys=gateway_keys,
        checkpointer=checkpointer,
        admit=gate.refusal,
        ledger=PostgresUsageLedger(settings.db_dsn) if settings.db_dsn else None,
    )
    # Step 28: approvals durable in the agent database, routed by OPA (rules without OPA)
    approval_gate = (
        PostgresApprovalGate(settings.db_dsn) if settings.db_dsn else InMemoryApprovalGate()
    )
    router = (
        OpaApprovalRouter(settings.opa_url)
        if settings.pdp.startswith("opa")
        else RulesApprovalRouter()
    )
    manager = RunManager(
        agents,
        assembly,
        store,
        desk=ApprovalDesk(approval_gate, router, audit),
        node=settings.node,  # Step 38: replicas share runs through leases
    )
    verifier = KeycloakTokenVerifier(
        settings.issuer, audience=settings.audience, jwks_url=settings.jwks_url
    )
    uvicorn.run(
        build_app(manager, verifier), host=settings.host, port=settings.port, log_level="info"
    )


if __name__ == "__main__":
    main()
