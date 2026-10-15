import json
import os
import stat
from pathlib import Path

import httpx
import pytest
import respx

from scripts.provision import keycloak, litellm_keys


@respx.mock
def test_keycloak_creates_client_once() -> None:
    url = "http://kc/admin/realms/r/clients"
    lookup = respx.get(url).mock(
        side_effect=[httpx.Response(200, json=[]), httpx.Response(200, json=[{"id": "1"}])]
    )
    create = respx.post(url).respond(201)
    with httpx.Client() as c:
        assert keycloak.provision(c, "http://kc", "r", "t").endswith("created")
        assert keycloak.provision(c, "http://kc", "r", "t").endswith("exists")
    assert lookup.call_count == 2
    assert create.call_count == 1
    sent = json.loads(create.calls[0].request.content)
    assert sent["serviceAccountsEnabled"] is True
    assert sent["standardFlowEnabled"] is False
    assert sent["publicClient"] is False


@respx.mock
def test_litellm_skips_existing_aliases_and_protects_key_file(tmp_path: Path) -> None:
    cfg = tmp_path / "keys.yaml"
    cfg.write_text(
        "roles:\n"
        "  planner: {models: [a], max_budget_usd: 1}\n"
        "  router: {models: [b], max_budget_usd: 2}\n"
    )
    respx.get("http://gw/key/list").respond(
        200, json={"keys": [{"key_alias": "agent-platform-planner"}]}
    )
    gen = respx.post("http://gw/key/generate").respond(200, json={"key": "sk-new"})
    out = tmp_path / "out.env"
    with httpx.Client() as c:
        actions = litellm_keys.provision(c, "http://gw", "mk", out, cfg)
    assert "key agent-platform-planner: exists" in actions
    assert "key agent-platform-router: created" in actions
    assert gen.call_count == 1
    body = json.loads(gen.calls[0].request.content)
    assert body["models"] == ["b"]
    assert body["max_budget"] == 2
    assert out.read_text() == "LITELLM_KEY_ROUTER=sk-new\n"
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert not any("sk-new" in a for a in actions), "key values must never be printed"


def test_role_config_covers_every_platform_role() -> None:
    roles, _ = litellm_keys.load_roles()
    assert set(roles) == {"planner", "executor", "router", "judge", "summariser"}


@pytest.mark.integration
def test_provisioning_is_idempotent_against_live_services(tmp_path: Path) -> None:
    from scripts.provision import agent_db

    admin = os.environ["P0_POSTGRES_ADMIN_DSN"]
    pw = os.environ["AGENT_DB_PASSWORD"]
    agent_db.provision(admin, pw)
    second = agent_db.provision(admin, pw)
    assert all(line.endswith("exists") for line in second), second

    base = os.environ["P0_LITELLM_URL"]
    master = os.environ["LITELLM_MASTER_KEY"]
    with httpx.Client(timeout=20) as c:
        litellm_keys.provision(c, base, master, tmp_path / "k.env")
        again = litellm_keys.provision(c, base, master, tmp_path / "k2.env")
    assert all(a.endswith("exists") for a in again), again
