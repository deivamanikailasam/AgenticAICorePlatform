"""Apply SQL migrations from libs/*/migrations/*.sql to the agent database, in order, once each.

Applied files are recorded in ``agent_runtime.schema_migrations`` with a SHA-256 of their content;
a changed, already-applied file is an error (write a new migration instead).
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent


def migration_files() -> list[Path]:
    files = list(ROOT.glob("libs/*/migrations/*.sql")) + list(
        ROOT.glob("services/*/migrations/*.sql")
    )
    return sorted(files, key=lambda p: (p.name, str(p)))


def migrate(dsn: str) -> list[str]:
    actions: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "create table if not exists agent_runtime.schema_migrations ("
            " id text primary key, sha256 text not null, applied_at timestamptz not null default now())"
        )
        rows = conn.execute("select id, sha256 from agent_runtime.schema_migrations").fetchall()
        done: dict[str, str] = {str(r[0]): str(r[1]) for r in rows}
        for f in migration_files():
            mid = f"{f.parent.parent.name}/{f.name}"
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            if mid in done:
                if done[mid] != digest:
                    raise SystemExit(
                        f"migrate: {mid} changed after it was applied; add a new migration"
                    )
                actions.append(f"{mid}: already applied")
                continue
            with conn.transaction():
                conn.execute(f.read_text())  # trusted repository SQL
                conn.execute(
                    "insert into agent_runtime.schema_migrations (id, sha256) values (%s, %s)",
                    (mid, digest),
                )
            actions.append(f"{mid}: applied")
    return actions


def main() -> int:
    try:  # in a service image: the DSN may be an OpenBao reference (Step 38)
        from agent_tools.secrets import resolve_env_references

        resolve_env_references(("AGENT_DB_DSN",))
    except ImportError:
        pass
    dsn = os.environ.get("AGENT_DB_DSN")
    if not dsn:
        print("set AGENT_DB_DSN (just with-secrets just migrate)")
        return 2
    for line in migrate(dsn):
        print(f"migrate: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
