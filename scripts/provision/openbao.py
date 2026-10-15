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
  into the dev secrets; it is never printed.

Needs ``P0_OPENBAO_URL``, ``OPENBAO_ADMIN_TOKEN`` (provisioning only) and
``P0_POSTGRES_ADMIN_DSN``.
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


def ensure_postgres(admin_dsn: str, db: dict[str, Any]) -> tuple[list[str], str | None]:
    """Group roles, schemas and the manager role. Returns the manager's new password when the
    manager role had to be created (OpenBao rotates it straight after)."""
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


def provision(
    bao: Bao, admin_dsn: str, cfg: dict[str, Any], db_host: str, out_dir: Path
) -> list[str]:
    actions = [
        ensure_mount(bao, cfg["kv_mount"], {"type": "kv", "options": {"version": "2"}}),
        ensure_policy(bao, RUNTIME_POLICY, runtime_policy(cfg)),
    ]
    pg_actions, new_password = ensure_postgres(admin_dsn, cfg["database"])
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
    with httpx.Client(timeout=15.0) as client:
        for line in provision(Bao(client, url, token), admin_dsn, cfg, host, out_dir):
            print(f"openbao: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
