"""Create Project A's paths, engines, policies and identities in Project 0's OpenBao. Idempotent.

What it sets up (``infra/provision/openbao.yaml``):

* a KV v2 mount for the platform's static secrets, and imports into it the LiteLLM role keys
  from ``.tmp/litellm-keys.env`` (written by ``just provision-litellm``), deleting the file once
  every key reads back;
* a database secrets engine on the ``agent`` database: a Postgres manager role whose password
  OpenBao rotates at once (nobody else knows it), and one OpenBao role per tool credential
  that creates a short-lived user in a read-only group role;
* the runtime's AppRole identity with a policy that may only *read* the KV mount. A new
  secret id is written once to ``.tmp/openbao-approle.env`` (0600, git-ignored) to be moved
  into the dev secrets; it is never printed;
* agent identities: the Keycloak client secrets of ``agent-platform`` and every ``agent-<name>``
  copied into KV (``keycloak/<client>``), JWT auth trusting the realm's signing keys, and per
  agent a role (``aud=openbao``, ``azp=agent-<name>``) and a policy that may issue only the
  credential sources its spec grants.

Needs ``P0_OPENBAO_URL``, ``OPENBAO_ADMIN_TOKEN`` (provisioning only), ``P0_POSTGRES_ADMIN_DSN``
and the Keycloak admin login (``KEYCLOAK_ADMIN_USER``, ``KEYCLOAK_ADMIN_PASSWORD``).
``AGENT_BAO_KEYCLOAK_URL`` is Keycloak as OpenBao reaches it (default ``http://keycloak:8080``);
``AGENT_BAO_JWT_ISSUER``, when set, is also required as the tokens' issuer.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

import httpx
import psycopg
import yaml
from psycopg import sql

from scripts.provision import keycloak
from scripts.provision.agents import AgentNeeds, agent_needs

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "infra" / "provision" / "openbao.yaml"
RUNTIME_POLICY = "agent-platform-runtime"
DURATION = re.compile(r"^(\d+)([smh])$")


def seconds(value: str | int) -> int:
    if isinstance(value, int):
        return value
    m = DURATION.match(value)
    if not m:
        raise ValueError(f"duration must look like 30s, 5m or 1h: {value}")
    return int(m[1]) * {"s": 1, "m": 60, "h": 3600}[m[2]]


def load(path: Path = CONFIG) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text()))


class Bao:
    def __init__(self, client: httpx.Client, url: str, token: str) -> None:
        self.client, self.url = client, url.rstrip("/")
        self.headers = {"X-Vault-Token": token}

    def get(self, path: str) -> Any:  # None when absent
        resp = self.client.get(f"{self.url}/v1/{path}", headers=self.headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def write(self, path: str, body: dict[str, Any] | None = None, method: str = "POST") -> Any:
        resp = self.client.request(
            method, f"{self.url}/v1/{path}", json=body or {}, headers=self.headers
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"OpenBao {method} {path}: {resp.status_code} {resp.text[:300]}")
        return resp.json() if resp.content else None


# ---------------------------------------------------------------- mounts and policies


def ensure_mount(bao: Bao, path: str, body: dict[str, Any], kind: str = "mounts") -> str:
    mounts = bao.get(f"sys/{'auth' if kind == 'auth' else 'mounts'}") or {}
    have = mounts.get("data", mounts)
    if f"{path}/" in have:
        return f"{kind[:-1] if kind == 'mounts' else 'auth'} {path}: exists"
    bao.write(f"sys/{'auth' if kind == 'auth' else 'mounts'}/{path}", body)
    return f"{kind[:-1] if kind == 'mounts' else 'auth'} {path}: created"


def runtime_policy(cfg: dict[str, Any]) -> str:
    return f'path "{cfg["kv_mount"]}/data/*" {{\n  capabilities = ["read"]\n}}\n'


def ensure_policy(bao: Bao, name: str, hcl: str) -> str:
    current = bao.get(f"sys/policies/acl/{name}")
    if current and current.get("data", {}).get("policy") == hcl:
        return f"policy {name}: exists"
    bao.write(f"sys/policies/acl/{name}", {"policy": hcl}, method="PUT")
    return f"policy {name}: {'updated' if current else 'created'}"


# ---------------------------------------------------------------- database credentials


def creation_statements(member_of: str) -> list[str]:
    return [
        "CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' "
        f"VALID UNTIL '{{{{expiration}}}}' IN ROLE {member_of};"
    ]


def revocation_statements(member_of: str) -> list[str]:
    # explicit: the engine's default (REVOKE ALL ON ALL TABLES ...) fails on tables the
    # manager role does not own
    return [
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = '{{name}}';",
        f'REVOKE {member_of} FROM "{{{{name}}}}";',
        'DROP ROLE IF EXISTS "{{name}}";',
    ]


def ensure_postgres(
    admin_dsn: str, db: dict[str, Any], reset_manager: bool = False
) -> tuple[list[str], str | None]:
    """Group roles, schemas and the manager role. Returns the manager's new password when the
    manager role had to be created, or reset because OpenBao has no connection for it (OpenBao
    lost its storage, or a restore); OpenBao rotates it straight after."""
    actions: list[str] = []
    manager = db["manager_role"]
    new_password: str | None = None
    dbname = db["database"]
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        if not conn.execute("select 1 from pg_roles where rolname = %s", (manager,)).fetchone():
            new_password = secrets.token_urlsafe(32)
            conn.execute(
                sql.SQL("create role {} login createrole password {}").format(
                    sql.Identifier(manager), sql.Literal(new_password)
                )
            )
            actions.append(f"postgres role {manager}: created")
        elif reset_manager:
            new_password = secrets.token_urlsafe(32)
            conn.execute(
                sql.SQL("alter role {} password {}").format(
                    sql.Identifier(manager), sql.Literal(new_password)
                )
            )
            actions.append(f"postgres role {manager}: password reset (OpenBao had no connection)")
        else:
            actions.append(f"postgres role {manager}: exists")
        conn.execute(
            sql.SQL("grant connect on database {} to {}").format(
                sql.Identifier(dbname), sql.Identifier(manager)
            )
        )
        # Postgres 16+: the manager inherits the users it creates, so revocation may end their
        # open sessions (pg_terminate_backend), not only drop the role
        conn.execute(
            sql.SQL("alter role {} set createrole_self_grant = 'inherit'").format(
                sql.Identifier(manager)
            )
        )
    with psycopg.connect(
        psycopg.conninfo.make_conninfo(admin_dsn, dbname=dbname), autocommit=True
    ) as conn:
        for role in db["roles"].values():
            group, schema = role["member_of"], role["schema"]
            if not conn.execute("select 1 from pg_roles where rolname = %s", (group,)).fetchone():
                conn.execute(sql.SQL("create role {} nologin").format(sql.Identifier(group)))
                actions.append(f"postgres role {group}: created")
            else:
                actions.append(f"postgres role {group}: exists")
            if not conn.execute(
                "select 1 from pg_namespace where nspname = %s", (schema,)
            ).fetchone():
                conn.execute(
                    sql.SQL("create schema {} authorization agent_app").format(
                        sql.Identifier(schema)
                    )
                )
                actions.append(f"schema {schema}: created")
            g, s = sql.Identifier(group), sql.Identifier(schema)
            for stmt in (
                sql.SQL("grant connect on database {} to {}").format(sql.Identifier(dbname), g),
                sql.SQL("grant usage on schema {} to {}").format(s, g),
                sql.SQL("grant select on all tables in schema {} to {}").format(s, g),
                sql.SQL(
                    "alter default privileges for role agent_app in schema {} grant select on tables to {}"
                ).format(s, g),
                sql.SQL("grant {} to {} with admin option").format(g, sql.Identifier(manager)),
            ):
                conn.execute(stmt)
    return actions, new_password


def ensure_database_engine(
    bao: Bao, db: dict[str, Any], host: str, new_password: str | None
) -> list[str]:
    actions = [ensure_mount(bao, db["mount"], {"type": "database"})]
    conn_path = f"{db['mount']}/config/{db['connection']}"
    if bao.get(conn_path) is None or new_password is not None:
        if new_password is None:
            raise RuntimeError(
                f"OpenBao has no connection {conn_path} but Postgres role {db['manager_role']} "
                "already exists; drop that role (nothing else uses it) and re-run"
            )
        bao.write(
            conn_path,
            {
                "plugin_name": "postgresql-database-plugin",
                "connection_url": (
                    f"postgresql://{{{{username}}}}:{{{{password}}}}@{host}/{db['database']}"
                    "?sslmode=disable"
                ),
                "username": db["manager_role"],
                "password": new_password,
                "allowed_roles": sorted(db["roles"]),
                "password_authentication": "scram-sha-256",
            },
        )
        bao.write(f"{db['mount']}/rotate-root/{db['connection']}")
        actions.append(f"connection {conn_path}: created, manager password rotated by OpenBao")
    else:
        current = bao.get(conn_path)["data"].get("allowed_roles") or []
        if sorted(current) != sorted(db["roles"]):
            # a partial write keeps the connection and its rotated password
            bao.write(conn_path, {"allowed_roles": sorted(db["roles"])})
            actions.append(f"connection {conn_path}: allowed roles updated")
        else:
            actions.append(f"connection {conn_path}: exists")
    for name, role in db["roles"].items():
        body = {
            "db_name": db["connection"],
            "creation_statements": creation_statements(role["member_of"]),
            "revocation_statements": revocation_statements(role["member_of"]),
            "default_ttl": seconds(role["default_ttl"]),
            "max_ttl": seconds(role["max_ttl"]),
        }
        if seconds(role["max_ttl"]) > 900:
            raise ValueError(f"database role {name}: max_ttl over 15 minutes is not allowed")
        current = (bao.get(f"{db['mount']}/roles/{name}") or {}).get("data")
        same = current is not None and all(
            current.get(k) == v for k, v in body.items() if k != "db_name"
        )
        if not same:
            bao.write(f"{db['mount']}/roles/{name}", body)
        actions.append(
            f"database role {name}: {'exists' if same else 'created' if current is None else 'updated'}"
        )
    return actions


# ---------------------------------------------------------------- runtime identity


def ensure_approle(bao: Bao, cfg: dict[str, Any], out: Path) -> list[str]:
    ar = cfg["approle"]
    actions = [ensure_mount(bao, "approle", {"type": "approle"}, kind="auth")]
    body = {
        "token_policies": [RUNTIME_POLICY],
        "token_ttl": seconds(ar["token_ttl"]),
        "token_max_ttl": seconds(ar["token_max_ttl"]),
        "secret_id_ttl": seconds(ar["secret_id_ttl"]),
        "token_type": "service",
    }
    path = f"auth/approle/role/{ar['role']}"
    existed = bao.get(path) is not None
    bao.write(path, body)
    actions.append(f"approle {ar['role']}: {'exists' if existed else 'created'}")
    if not existed or not out.exists():
        role_id = bao.get(f"{path}/role-id")["data"]["role_id"]
        secret_id = bao.write(f"{path}/secret-id")["data"]["secret_id"]
        out.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(f"AGENT_BAO_ROLE_ID={role_id}\nAGENT_BAO_SECRET_ID={secret_id}\n")
        actions.append(
            f"approle {ar['role']}: new secret id written to {out} (move it, then delete)"
        )
    return actions


def import_litellm_keys(bao: Bao, cfg: dict[str, Any], keys_file: Path) -> list[str]:
    if not keys_file.exists():
        return ["litellm keys: nothing to import"]
    actions = []
    pairs = [
        line.split("=", 1)
        for line in keys_file.read_text().splitlines()
        if line.startswith("LITELLM_KEY_") and "=" in line
    ]
    for name, value in pairs:
        role = name.removeprefix("LITELLM_KEY_").lower()
        path = f"{cfg['kv_mount']}/data/litellm/{role}"
        bao.write(path, {"data": {"api_key": value}})
        stored = bao.get(path)["data"]["data"]["api_key"]
        if stored != value:
            raise RuntimeError(f"litellm/{role} did not read back; keeping {keys_file}")
        actions.append(f"litellm key {role}: stored in {cfg['kv_mount']}/litellm/{role}")
    keys_file.unlink()
    actions.append(f"litellm keys: {keys_file} deleted")
    return actions


# ---------------------------------------------------------------- agent identities


def agent_policy(agent: AgentNeeds) -> str:
    """Issue and end leases for the agent's granted credential sources; nothing else."""
    rules = [
        f'path "{src}" {{\n  capabilities = ["read"]\n}}\n'
        f'path "sys/leases/revoke/{src}/*" {{\n  capabilities = ["update"]\n}}\n'
        for src in agent.credentials
    ]
    if rules:
        rules.append('path "sys/leases/lookup" {\n  capabilities = ["update"]\n}\n')
    return "".join(rules) or "# this agent's tools are granted no credentials\n"


def store_client_secrets(
    bao: Bao,
    cfg: dict[str, Any],
    kc: httpx.Client,
    admin: str,
    headers: dict[str, str],
    clients: list[str],
) -> list[str]:
    actions = []
    for client_id in clients:
        found = kc.get(f"{admin}/clients", params={"clientId": client_id}, headers=headers).json()
        if not found:
            raise RuntimeError(f"Keycloak has no client {client_id}: run just provision-keycloak")
        secret = kc.get(f"{admin}/clients/{found[0]['id']}/client-secret", headers=headers).json()[
            "value"
        ]
        path = f"{cfg['kv_mount']}/data/keycloak/{client_id}"
        current = bao.get(path)
        if current and current["data"]["data"].get("client_secret") == secret:
            actions.append(f"secret keycloak/{client_id}: exists")
            continue
        bao.write(path, {"data": {"client_secret": secret}})
        actions.append(f"secret keycloak/{client_id}: {'updated' if current else 'stored'}")
    return actions


def ensure_agent_identities(
    bao: Bao, agents: list[AgentNeeds], jwks_url: str, issuer: str | None
) -> list[str]:
    actions = [ensure_mount(bao, "jwt", {"type": "jwt"}, kind="auth")]
    body: dict[str, Any] = {"jwks_url": jwks_url, "jwt_supported_algs": ["RS256"]}
    if issuer:
        body["bound_issuer"] = issuer
    current = (bao.get("auth/jwt/config") or {}).get("data") or {}
    if all(current.get(k) == v for k, v in body.items()):
        actions.append("jwt config: exists")
    else:
        bao.write("auth/jwt/config", body)
        actions.append(f"jwt config: {'updated' if current else 'created'}")
    for agent in agents:
        name = f"{keycloak.AGENT_PREFIX}{agent.name}"
        actions.append(ensure_policy(bao, name, agent_policy(agent)))
        role = {
            "role_type": "jwt",
            "bound_audiences": [keycloak.OPENBAO_AUDIENCE],
            "bound_claims": {"azp": name},
            "user_claim": "azp",
            "token_policies": [name],
            "token_ttl": 600,
            "token_max_ttl": 900,
            "token_no_default_policy": True,
        }
        existing = (bao.get(f"auth/jwt/role/{name}") or {}).get("data")
        same = existing is not None and all(existing.get(k) == v for k, v in role.items())
        if not same:
            bao.write(f"auth/jwt/role/{name}", role)
        actions.append(
            f"jwt role {name}: {'exists' if same else 'created' if existing is None else 'updated'}"
        )
    return actions


def provision(
    bao: Bao, admin_dsn: str, cfg: dict[str, Any], db_host: str, out_dir: Path
) -> list[str]:
    actions = [
        ensure_mount(bao, cfg["kv_mount"], {"type": "kv", "options": {"version": "2"}}),
        ensure_policy(bao, RUNTIME_POLICY, runtime_policy(cfg)),
    ]
    db = cfg["database"]
    connected = bao.get(f"{db['mount']}/config/{db['connection']}") is not None
    pg_actions, new_password = ensure_postgres(admin_dsn, db, reset_manager=not connected)
    actions += pg_actions
    actions += ensure_database_engine(bao, cfg["database"], db_host, new_password)
    actions += ensure_approle(bao, cfg, out_dir / "openbao-approle.env")
    actions += import_litellm_keys(bao, cfg, out_dir / "litellm-keys.env")
    return actions


def main() -> int:
    url = os.environ.get("P0_OPENBAO_URL")
    token = os.environ.get("OPENBAO_ADMIN_TOKEN")
    admin_dsn = os.environ.get("P0_POSTGRES_ADMIN_DSN")
    if not url or not token or not admin_dsn:
        print(
            "set P0_OPENBAO_URL, OPENBAO_ADMIN_TOKEN and P0_POSTGRES_ADMIN_DSN (just with-secrets ...)"
        )
        return 2
    cfg = load()
    host = os.environ.get("AGENT_BAO_DB_HOST", cfg["database"]["host"])
    out_dir = Path(os.environ.get("AGENT_PROVISION_OUT", ROOT / ".tmp"))
    kc_base = os.environ.get("P0_KEYCLOAK_URL", "http://127.0.0.1:8080")
    realm = os.environ.get("P0_KEYCLOAK_REALM", "rag-platform")
    kc_user, kc_password = (
        os.environ.get("KEYCLOAK_ADMIN_USER"),
        os.environ.get("KEYCLOAK_ADMIN_PASSWORD"),
    )
    if not kc_user or not kc_password:
        print("set KEYCLOAK_ADMIN_USER and KEYCLOAK_ADMIN_PASSWORD (just with-secrets ...)")
        return 2
    bao_kc = os.environ.get("AGENT_BAO_KEYCLOAK_URL", "http://keycloak:8080")
    jwks = f"{bao_kc}/realms/{realm}/protocol/openid-connect/certs"
    agents = agent_needs()
    with httpx.Client(timeout=15.0) as client:
        bao = Bao(client, url, token)
        lines = provision(bao, admin_dsn, cfg, host, out_dir)
        kc_token = keycloak.admin_token(client, kc_base, kc_user, kc_password)
        lines += store_client_secrets(
            bao,
            cfg,
            client,
            f"{kc_base}/admin/realms/{realm}",
            {"Authorization": f"Bearer {kc_token}"},
            [keycloak.CLIENT_ID, *(f"{keycloak.AGENT_PREFIX}{a.name}" for a in agents)],
        )
        lines += ensure_agent_identities(bao, agents, jwks, os.environ.get("AGENT_BAO_JWT_ISSUER"))
        for line in lines:
            print(f"openbao: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
