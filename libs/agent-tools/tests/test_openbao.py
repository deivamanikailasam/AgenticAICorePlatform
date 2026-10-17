"""OpenBao client, secret resolver and credential broker (Step 29).

Unit tests use a mock transport. Integration tests run the shared credential-broker contract,
real database logins, revocation and expiry against OpenBao and Postgres (stand-in in CI,
Project 0 on the Mac) after ``just provision-openbao``.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
import pytest
from agent_contracts.testing import CredentialBrokerContract
from agent_contracts.testing.contracts import ctx
from agent_tools.openbao import (
    AppRoleAuth,
    JwtAuth,
    KubernetesAuth,
    OpenBaoClient,
    OpenBaoCredentialBroker,
    OpenBaoSecretResolver,
    TokenAuth,
    VaultCredentialBroker,
)

BAO = os.environ.get("P0_OPENBAO_URL", "http://127.0.0.1:8200")
ADMIN = os.environ.get("OPENBAO_ADMIN_TOKEN", "standin-only")
PG_HOST = os.environ.get("AGENT_DB_HOST", "127.0.0.1:5432")
SOURCE = "agent-db/creds/tool-reader"


def _bao(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ---------------------------------------------------------------- unit


async def test_resolver_reads_kv_v2_fields() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert req.url.path == "/v1/agent-platform/data/keycloak/agent-platform"
        return httpx.Response(200, json={"data": {"data": {"client_secret": "s3"}}})

    client = OpenBaoClient("http://bao", TokenAuth("t"), transport=_bao(handler))
    resolver = OpenBaoSecretResolver(client)
    assert await resolver.resolve("${secret:keycloak/agent-platform#client_secret}") == "s3"
    with pytest.raises(KeyError, match="no field nope"):
        await resolver.resolve("${secret:keycloak/agent-platform#nope}")
    with pytest.raises(KeyError, match="not a secret reference"):
        await resolver.resolve("plain")
    assert seen[0].headers["X-Vault-Token"] == "t"


async def test_missing_and_forbidden_paths_raise_typed_errors() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "missing" in req.url.path:
            return httpx.Response(404, json={"errors": []})
        return httpx.Response(403, json={"errors": ["permission denied"]})

    resolver = OpenBaoSecretResolver(
        OpenBaoClient("http://bao", TokenAuth("t"), transport=_bao(handler))
    )
    with pytest.raises(KeyError, match="not found in OpenBao"):
        await resolver.resolve("${secret:missing#k}")
    with pytest.raises(PermissionError, match="permission denied"):
        await resolver.resolve("${secret:other#k}")


async def test_approle_and_jwt_logins_and_relogin_before_expiry() -> None:
    logins: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/login"):
            logins.append({"path": req.url.path, **json.loads(req.content)})
            return httpx.Response(200, json={"auth": {"client_token": "tok", "lease_duration": 20}})
        assert req.headers["X-Vault-Token"] == "tok"
        return httpx.Response(200, json={"data": {"data": {"k": "v"}}})

    approle = OpenBaoClient(
        "http://bao", AppRoleAuth("role-1", "secret-1"), transport=_bao(handler)
    )
    await approle.request("GET", "agent-platform/data/x")
    # a 20 s token is inside the 30 s renewal margin: the next request logs in again
    await approle.request("GET", "agent-platform/data/x")
    assert [entry["path"] for entry in logins] == ["/v1/auth/approle/login"] * 2
    assert logins[0]["role_id"] == "role-1"

    async def agent_token() -> str:
        return "jwt-of-agent"

    jwt = OpenBaoClient("http://bao", JwtAuth("agent-triage", agent_token), transport=_bao(handler))
    await jwt.request("GET", "agent-platform/data/x")
    assert logins[-1] == {
        "path": "/v1/auth/jwt/login",
        "role": "agent-triage",
        "jwt": "jwt-of-agent",
    }


async def test_kubernetes_login_reads_the_rotated_service_account_token(tmp_path: Any) -> None:
    token = tmp_path / "token"
    logins: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/login"):
            logins.append({"path": req.url.path, **json.loads(req.content)})
            return httpx.Response(200, json={"auth": {"client_token": "tok", "lease_duration": 20}})
        return httpx.Response(200, json={"data": {"data": {"k": "v"}}})

    client = OpenBaoClient(
        "http://bao", KubernetesAuth("agent-platform", str(token)), transport=_bao(handler)
    )
    with pytest.raises(PermissionError, match="no service-account token"):
        await client.request("GET", "agent-platform/data/x")
    token.write_text("sa-jwt-1\n")
    await client.request("GET", "agent-platform/data/x")
    token.write_text("sa-jwt-2")  # the kubelet rotated it; the next login uses the new one
    await client.request("GET", "agent-platform/data/x")
    assert logins == [
        {"path": "/v1/auth/kubernetes/login", "role": "agent-platform", "jwt": "sa-jwt-1"},
        {"path": "/v1/auth/kubernetes/login", "role": "agent-platform", "jwt": "sa-jwt-2"},
    ]


def test_client_survives_successive_event_loops() -> None:
    """Services resolve secrets with asyncio.run() before serving in a new loop (Step 38): a
    keep-alive connection from the first loop must not be reused in the second."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            body = json.dumps({"data": {"data": {"dsn": "postgresql://x"}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        resolver = OpenBaoSecretResolver(
            OpenBaoClient(f"http://127.0.0.1:{server.server_port}", TokenAuth("t"))
        )
        first = asyncio.run(resolver.resolve("${secret:agent-platform/db#dsn}"))
        second = asyncio.run(resolver.resolve("${secret:agent-platform/db#dsn}"))
        assert first == second == "postgresql://x"
    finally:
        server.shutdown()


def test_staging_resolver_uses_kubernetes_auth_and_never_the_environment() -> None:
    from agent_tools.openbao import OpenBaoSecretResolver
    from agent_tools.secrets import default_resolver

    env = {"AGENT_ENV": "prod", "P0_OPENBAO_URL": "http://bao", "AGENT_BAO_K8S_ROLE": "r"}
    resolver = default_resolver(env)
    assert isinstance(resolver, OpenBaoSecretResolver)
    assert isinstance(resolver.client.auth, KubernetesAuth)
    with pytest.raises(RuntimeError, match="AGENT_BAO_K8S_ROLE"):
        default_resolver({"AGENT_ENV": "prod"})


async def test_refused_login_is_a_permission_error_without_the_secret() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errors": ["invalid role or secret ID"]})

    client = OpenBaoClient(
        "http://bao", AppRoleAuth("r", "very-secret-id"), transport=_bao(handler)
    )
    with pytest.raises(PermissionError) as err:
        await client.request("GET", "x")
    assert "very-secret-id" not in str(err.value)


async def test_broker_refuses_long_or_missing_leases_and_revokes_them() -> None:
    revoked: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/v1/sys/leases/revoke/"):
            revoked.append(req.url.path.removeprefix("/v1/sys/leases/revoke/"))
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={"lease_id": "db/creds/r/1", "lease_duration": 86400, "data": {"password": "p"}},
        )

    broker = OpenBaoCredentialBroker("http://bao", TokenAuth("t"), transport=_bao(handler))
    with pytest.raises(PermissionError, match="must expire within 900s"):
        await broker.issue("db/creds/r", ctx())
    assert revoked == ["db/creds/r/1"]
    with pytest.raises(KeyError, match="<mount>/creds/<role>"):
        await broker.issue("db/static/r", ctx())


async def test_broker_logs_in_as_each_agent() -> None:
    roles: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/login"):
            roles.append(json.loads(req.content)["role"])
            return httpx.Response(200, json={"auth": {"client_token": "t", "lease_duration": 600}})
        return httpx.Response(
            200, json={"lease_id": "db/creds/r/2", "lease_duration": 60, "data": {"u": "x"}}
        )

    async def token() -> str:
        return "jwt"

    broker = OpenBaoCredentialBroker(
        "http://bao",
        agent_auth=lambda agent: JwtAuth(f"agent-{agent}", token),
        transport=_bao(handler),
    )
    cred = await broker.issue("db/creds/r", ctx())
    await broker.issue("db/creds/r", ctx())
    assert roles == ["agent-a1"]  # one login per agent, then the token is reused
    assert cred.issued_to == "a1"
    assert cred.source == "openbao:db/creds/r"


def test_vault_is_the_same_api() -> None:
    assert issubclass(VaultCredentialBroker, OpenBaoCredentialBroker)


# ---------------------------------------------------------------- integration


def _admin() -> OpenBaoClient:
    return OpenBaoClient(BAO, TokenAuth(ADMIN))


@pytest.mark.integration
class TestOpenBaoCredentialBroker(CredentialBrokerContract):
    source = SOURCE
    unknown_source = "agent-db/creds/no-such-role"

    def make(self) -> OpenBaoCredentialBroker:
        return OpenBaoCredentialBroker(BAO, TokenAuth(ADMIN))


async def _login(cred: Any) -> Any:
    import psycopg

    return await psycopg.AsyncConnection.connect(
        f"postgresql://{cred.secret('username')}:{cred.secret('password')}@{PG_HOST}/agent",
        autocommit=True,
        connect_timeout=5,
    )


@pytest.mark.integration
async def test_issued_database_user_is_read_only_and_revocation_ends_it() -> None:
    import psycopg

    broker = OpenBaoCredentialBroker(BAO, TokenAuth(ADMIN))
    cred = await broker.issue(SOURCE, ctx())
    try:
        assert cred.ttl_seconds() <= 300  # provisioned default_ttl: 5 minutes
        conn = await _login(cred)
        await conn.execute("select count(*) from pg_tables where schemaname = 'tool_data'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("select * from agent_runtime.usage_ledger limit 1")
    finally:
        await broker.revoke(cred)
    assert not await broker.valid(cred)
    with pytest.raises(psycopg.OperationalError):
        await conn.execute("select 1")  # the open session is ended too
    with pytest.raises(psycopg.OperationalError):
        await _login(cred)
    await broker.aclose()


@pytest.mark.integration
async def test_a_leaked_credential_expires_on_its_own() -> None:
    """A lease nobody revokes still dies: here a 3-second role, provisioned roles 5 minutes."""
    import psycopg

    admin = _admin()
    role = "agent-db/roles/test-expiry"
    current = await admin.request("GET", "agent-db/roles/tool-reader")
    body = {k: current["data"][k] for k in ("creation_statements", "revocation_statements")}
    await admin.request("POST", role, {"db_name": "agent", **body, "default_ttl": 3, "max_ttl": 3})
    await admin.request(
        "POST", "agent-db/config/agent", {"allowed_roles": ["tool-reader", "test-expiry"]}
    )
    broker = OpenBaoCredentialBroker(BAO, TokenAuth(ADMIN))
    try:
        cred = await broker.issue("agent-db/creds/test-expiry", ctx())
        assert cred.ttl_seconds() <= 3
        conn = await _login(cred)
        await conn.close()
        await asyncio.sleep(5)
        assert not await broker.valid(cred)
        with pytest.raises(psycopg.OperationalError):
            await _login(cred)
    finally:
        await admin.request("POST", "agent-db/config/agent", {"allowed_roles": ["tool-reader"]})
        await admin.request("DELETE", role)
        await broker.aclose()
        await admin.aclose()


@pytest.mark.integration
async def test_runtime_identity_may_read_kv_but_not_issue_tool_credentials() -> None:
    """The runtime's AppRole policy: KV read only. Tool credentials need an agent identity."""
    admin = _admin()
    await admin.request("POST", "agent-platform/data/test/probe", {"data": {"value": "readable"}})
    role_id = (await admin.request("GET", "auth/approle/role/agent-platform-runtime/role-id"))[
        "data"
    ]["role_id"]
    secret = await admin.request("POST", "auth/approle/role/agent-platform-runtime/secret-id")
    runtime = OpenBaoClient(BAO, AppRoleAuth(role_id, secret["data"]["secret_id"]))
    try:
        resolver = OpenBaoSecretResolver(runtime)
        assert await resolver.resolve("${secret:test/probe#value}") == "readable"
        with pytest.raises(PermissionError):
            await resolver.write("test/probe", {"value": "changed"})
        with pytest.raises(PermissionError):
            await runtime.request("GET", SOURCE)
    finally:
        await admin.request(
            "POST",
            "auth/approle/role/agent-platform-runtime/secret-id-accessor/destroy",
            {"secret_id_accessor": secret["data"]["secret_id_accessor"]},
        )
        await admin.request("DELETE", "agent-platform/metadata/test/probe")
        await runtime.aclose()
        await admin.aclose()


def test_connection_strings_can_be_openbao_references() -> None:
    from agent_tools.secrets import SecretResolver, resolve_env_references

    class Fixed(SecretResolver):
        async def resolve(self, reference: str) -> str:
            assert reference == "${secret:agent-platform/db#dsn}"
            return "postgresql://agent:pw@db/agent"

    env = {
        "AGENT_DB_DSN": "${secret:agent-platform/db#dsn}",
        "AGENT_AUDIT_DSN": "postgresql://audit@db/audit",  # not a reference: left alone
    }
    assert resolve_env_references(resolver=Fixed(), env=env) == ["AGENT_DB_DSN"]
    assert env == {
        "AGENT_DB_DSN": "postgresql://agent:pw@db/agent",
        "AGENT_AUDIT_DSN": "postgresql://audit@db/audit",
    }
    assert resolve_env_references(resolver=Fixed(), env={}) == []
