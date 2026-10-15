"""``approval_gate.postgres`` against the stand-in Postgres (or Project 0)."""

import os
import uuid

import psycopg
import pytest
from agent_contracts.models import ApprovalRequest
from agent_contracts.testing import ApprovalGateContract
from agent_contracts.testing.contracts import ctx
from agent_runtime.approval_store import PostgresApprovalGate

pytestmark = pytest.mark.integration


def dsn() -> str:
    value = os.environ.get("AGENT_DB_DSN")
    if not value:
        pytest.skip("AGENT_DB_DSN not set")
    return value


@pytest.fixture(autouse=True)
def _clean() -> None:
    with psycopg.connect(dsn(), autocommit=True) as conn:
        conn.execute("delete from agent_runtime.approvals where tenant_id = 't1'")


class TestPostgresApprovalGate(ApprovalGateContract):
    def make(self) -> PostgresApprovalGate:
        return PostgresApprovalGate(dsn())


async def test_a_request_survives_a_new_gate_instance() -> None:
    req = ApprovalRequest(id=uuid.uuid4().hex, ctx=ctx(), tool="pay", arguments={"amount": 1})
    await PostgresApprovalGate(dsn()).request(req)
    again = await PostgresApprovalGate(dsn()).get(req.id)
    assert again.request.arguments == {"amount": 1}
