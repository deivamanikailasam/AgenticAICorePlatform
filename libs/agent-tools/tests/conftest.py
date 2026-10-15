import os
import uuid

import pytest


@pytest.fixture
def namespace() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def agent_dsn() -> str:
    dsn = os.environ.get("AGENT_DB_DSN")
    if not dsn:
        pytest.skip("AGENT_DB_DSN not set")
    return dsn
