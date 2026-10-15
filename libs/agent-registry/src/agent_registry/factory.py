"""Which registry this process uses, from the environment.

``AGENT_REGISTRY``: ``postgres`` (default when ``AGENT_DB_DSN`` is set; namespace
``AGENT_REGISTRY_NAMESPACE``), ``backstage`` (directory ``AGENT_REGISTRY_CATALOG``,
default ``catalog/agents``), ``sqlite`` (file ``AGENT_REGISTRY_FILE``, default ``.agents.db``;
the default without a database) or ``memory``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from agent_contracts.dummies.registry import InMemoryAgentRegistry, SqliteAgentRegistry
from agent_contracts.interfaces import AgentRegistry

from agent_registry.gate import ENFORCED


def open_registry(env: Mapping[str, str] | None = None) -> tuple[AgentRegistry, str]:
    e = os.environ if env is None else env
    choice = e.get("AGENT_REGISTRY") or ("postgres" if e.get("AGENT_DB_DSN") else "sqlite")
    environment = e.get("AGENT_ENV", "dev")
    if environment in ENFORCED and choice in ("sqlite", "memory"):
        raise SystemExit(
            f"AGENT_ENV={environment} enforces approvals: use the shared registry "
            "(AGENT_REGISTRY=postgres with AGENT_DB_DSN, or backstage), not a local one"
        )
    if choice == "postgres":
        from agent_registry.postgres import PostgresAgentRegistry

        dsn = e.get("AGENT_DB_DSN")
        if not dsn:
            raise SystemExit("AGENT_REGISTRY=postgres needs AGENT_DB_DSN")
        ns = e.get("AGENT_REGISTRY_NAMESPACE", "default")
        return PostgresAgentRegistry(dsn, ns), f"postgres (namespace {ns})"
    if choice == "backstage":
        from agent_registry.backstage import BackstageCatalogRegistry

        directory = e.get("AGENT_REGISTRY_CATALOG", "catalog/agents")
        return BackstageCatalogRegistry(directory), f"backstage catalogue {directory}"
    if choice == "sqlite":
        path = e.get("AGENT_REGISTRY_FILE", ".agents.db")
        return SqliteAgentRegistry(path), f"local file {path}"
    if choice == "memory":
        return InMemoryAgentRegistry(), "in memory"
    raise SystemExit(f"AGENT_REGISTRY: unknown registry {choice!r}")
