"""Create Project A's `agent` database, its application role and schemas in Project 0 Postgres.

Idempotent: running it twice changes nothing the second time. Needs an admin DSN
(``P0_POSTGRES_ADMIN_DSN``) and the application password (``AGENT_DB_PASSWORD``), both from
SOPS-encrypted dev secrets.
"""

from __future__ import annotations

import os
import sys

import psycopg
from psycopg import sql

DB_NAME = "agent"
APP_ROLE = "agent_app"
SCHEMAS = ("agent_runtime", "agent_memory", "agent_registry", "agent_tools")
EXTENSIONS = ("vector", "pgcrypto")


def _with_db(dsn: str, dbname: str) -> str:
    return psycopg.conninfo.make_conninfo(dsn, dbname=dbname)


def provision(admin_dsn: str, app_password: str) -> list[str]:
    actions: list[str] = []
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute("select 1 from pg_roles where rolname = %s", (APP_ROLE,)).fetchone()
        if exists:
            actions.append(f"role {APP_ROLE}: exists")
        else:
            conn.execute(
                sql.SQL("create role {} login password {}").format(
                    sql.Identifier(APP_ROLE), sql.Literal(app_password)
                )
            )
            actions.append(f"role {APP_ROLE}: created")
        db = conn.execute("select 1 from pg_database where datname = %s", (DB_NAME,)).fetchone()
        if db:
            actions.append(f"database {DB_NAME}: exists")
        else:
            conn.execute(
                sql.SQL("create database {} owner {}").format(
                    sql.Identifier(DB_NAME), sql.Identifier(APP_ROLE)
                )
            )
            actions.append(f"database {DB_NAME}: created")

    with psycopg.connect(_with_db(admin_dsn, DB_NAME), autocommit=True) as conn:
        for ext in EXTENSIONS:
            have = conn.execute("select 1 from pg_extension where extname = %s", (ext,)).fetchone()
            if not have:
                conn.execute(sql.SQL("create extension {}").format(sql.Identifier(ext)))
            actions.append(f"extension {ext}: {'exists' if have else 'created'}")
        for schema in SCHEMAS:
            have = conn.execute(
                "select 1 from pg_namespace where nspname = %s", (schema,)
            ).fetchone()
            if not have:
                conn.execute(
                    sql.SQL("create schema {} authorization {}").format(
                        sql.Identifier(schema), sql.Identifier(APP_ROLE)
                    )
                )
            actions.append(f"schema {schema}: {'exists' if have else 'created'}")
        conn.execute(
            sql.SQL("revoke all on database {} from public").format(sql.Identifier(DB_NAME))
        )
    return actions


def main() -> int:
    admin_dsn = os.environ.get("P0_POSTGRES_ADMIN_DSN")
    password = os.environ.get("AGENT_DB_PASSWORD")
    if not admin_dsn or not password:
        print("set P0_POSTGRES_ADMIN_DSN and AGENT_DB_PASSWORD (just with-secrets ...)")
        return 2
    for line in provision(admin_dsn, password):
        print(f"agent-db: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
