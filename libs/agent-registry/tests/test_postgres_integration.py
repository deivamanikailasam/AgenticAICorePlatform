"""``agent_registry.postgres`` against the stand-in Postgres (or Project 0)."""

import os
import uuid

import pytest
from agent_contracts.testing import AgentRegistryContract
from agent_registry.postgres import PostgresAgentRegistry

pytestmark = pytest.mark.integration


def dsn() -> str:
    value = os.environ.get("AGENT_DB_DSN")
    if not value:
        pytest.skip("AGENT_DB_DSN not set")
    return value


class TestPostgresAgentRegistry(AgentRegistryContract):
    def make(self) -> PostgresAgentRegistry:
        return PostgresAgentRegistry(dsn(), namespace=f"test-{uuid.uuid4().hex[:8]}")
