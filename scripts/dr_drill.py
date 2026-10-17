"""Disaster-recovery drill for the agent database (Step 38, governance/dr.yaml).

    just dr-drill [--write-seconds 90]

What it does, end to end, on its own throw-away Postgres (the stand-in's image, pinned):

1. starts Postgres with continuous WAL archiving (``archive_timeout`` from dr.yaml) into an archive
   volume that stands in for off-host object storage, provisions the agent database and applies
   every migration;
2. takes a base backup, then keeps writing through the platform's own stores, once a second:
   encrypted checkpoints, runs with their events, long-term memories (pgvector) and hash-chained
   audit events;
3. **the disaster**: removes the database container and its data volume while writes go on;
4. restores: the base backup plus the archived WAL, replayed to the end (point-in-time recovery);
5. verifies: the audit chain checks out end to end; every checkpoint, run, event and memory found
   decrypts and matches what was written; memory search answers;
6. measures the recovery point (the newest acknowledged write that survived, against the moment
   of the disaster) and the time from the disaster to a verified database, compares both with the
   targets, and writes the result to evals/results/dr/.

Exit 1 if a target is missed or verification fails. Needs Docker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import yaml
from agent_api.store import PostgresRunStore
from agent_audit.postgres import PostgresAuditSink
from agent_contracts.dummies.embedder import HashingEmbedder
from agent_contracts.models import (
    AuditEvent,
    Checkpoint,
    MemoryItem,
    MemoryScope,
    Provenance,
    RunContext,
    Trust,
)
from agent_memory.pgvector import PgVectorMemoryStore
from agent_runtime.checkpoints import FieldCipher, PostgresCheckpointer

from scripts.migrate import migrate
from scripts.provision.agent_db import provision

ROOT = Path(__file__).resolve().parents[1]
# the stand-in's Postgres (tests/stack/compose.p0-standin.yaml), so the drill restores the same
# major version and extensions the platform runs on
IMAGE = (
    "docker.io/pgvector/pgvector:0.8.7-pg17-trixie"
    "@sha256:7a7e9f22015b67edb4bef5c59daeebcd7e74bfa570df6ce60ae01237c8648a84"
)
NAME = "agent-dr-pg"
PORT = 55432
VOLUMES = {
    "data": "agent-dr-data",
    "restored": "agent-dr-restored",
    "archive": "agent-dr-archive",
    "base": "agent-dr-base",
}
ADMIN_PASSWORD = "drill-only"  # noqa: S105 - a throw-away database on 127.0.0.1
APP_PASSWORD = "drill-only-app"  # noqa: S105
TENANT, USER = "acme", "dr-drill"


def docker(*args: str, check: bool = True) -> str:
    return subprocess.run(  # noqa: S603
        ["docker", *args],  # noqa: S607
        check=check,
        capture_output=True,
        text=True,
    ).stdout.strip()


def admin_dsn() -> str:
    return f"postgresql://postgres:{ADMIN_PASSWORD}@127.0.0.1:{PORT}/postgres"


def app_dsn() -> str:
    return f"postgresql://agent_app:{APP_PASSWORD}@127.0.0.1:{PORT}/agent"


def start(volume: str, archive_timeout: int, recovering: bool = False) -> None:
    settings = (
        ["-c", "restore_command=cp /archive/%f %p", "-c", "recovery_target_timeline=latest"]
        if recovering
        else [
            "-c",
            "wal_level=replica",
            "-c",
            "archive_mode=on",
            "-c",
            "archive_command=test ! -f /archive/%f && cp %p /archive/%f",
            "-c",
            f"archive_timeout={archive_timeout}",
        ]
    )
    docker(
        "run", "-d", "--name", NAME, "-e", f"POSTGRES_PASSWORD={ADMIN_PASSWORD}",
        "-v", f"{volume}:/var/lib/postgresql/data", "-v", f"{VOLUMES['archive']}:/archive",
        "-v", f"{VOLUMES['base']}:/base", "-p", f"127.0.0.1:{PORT}:5432", IMAGE,
        "postgres", *settings,
    )  # fmt: skip


def wait_ready(timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(admin_dsn(), connect_timeout=2) as conn:
                if not conn.execute("select pg_is_in_recovery()").fetchone()[0]:  # type: ignore[index]
                    return
        except psycopg.OperationalError:
            pass
        time.sleep(0.5)
    raise SystemExit(
        f"dr-drill: Postgres did not become ready: {docker('logs', '--tail', '30', NAME, check=False)}"
    )


def cleanup() -> None:
    docker("rm", "-f", NAME, check=False)
    for v in VOLUMES.values():
        docker("volume", "rm", "-f", v, check=False)


@dataclass
class Written:
    """What the workload wrote, and when each write was acknowledged."""

    checkpoints: dict[str, tuple[float, dict[str, Any]]] = field(default_factory=dict)
    runs: dict[str, tuple[float, str]] = field(default_factory=dict)
    memories: dict[str, tuple[float, str]] = field(default_factory=dict)
    audit: list[float] = field(default_factory=list)  # ack time of seq 0, 1, 2 ...

    def acked(self) -> list[float]:
        return sorted(
            [t for t, _ in self.checkpoints.values()]
            + [t for t, _ in self.runs.values()]
            + [t for t, _ in self.memories.values()]
            + self.audit
        )


class Workload:
    def __init__(self, key: bytes) -> None:
        cipher = FieldCipher(key)
        self.checkpoints = PostgresCheckpointer(app_dsn(), cipher)
        self.runs = PostgresRunStore(app_dsn(), cipher)
        self.memory = PgVectorMemoryStore(app_dsn(), HashingEmbedder(), cipher)
        self.audit = PostgresAuditSink(app_dsn())
        self.written = Written()
        self.scope = MemoryScope(tenant_id=TENANT, user_id=USER, agent_id="dr-drill")

    async def once(self, i: int) -> None:
        ctx = RunContext(tenant_id=TENANT, user_id=USER, agent_id="dr-drill", agent_version="0.0.1")
        point = Checkpoint(
            thread_id=ctx.thread_id,
            tenant_id=TENANT,
            run_id=ctx.run_id,
            step=1,
            state={"i": i, "total": 40 + i},
        )
        await self.checkpoints.put(point)
        self.written.checkpoints[point.checkpoint_id] = (time.time(), point.state)
        await self.runs.save(
            {"run_id": ctx.run_id, "tenant": TENANT, "user": USER, "thread_id": ctx.thread_id,
             "agent": "dr-drill", "status": "succeeded", "inputs": {"i": i}}
        )  # fmt: skip
        await self.runs.append(ctx.run_id, 0, {"type": "RUN_FINISHED", "i": i})
        self.written.runs[ctx.run_id] = (time.time(), point.thread_id)
        item = MemoryItem(
            scope=self.scope,
            content=f"drill fact number {i}",
            provenance=Provenance(source="user", trust=Trust.TRUSTED, validated=True),
        )
        item_id = await self.memory.put(item)
        self.written.memories[item_id] = (time.time(), item.content)
        await self.audit.append(
            AuditEvent(type="run.ended", ctx=ctx, payload={"status": "succeeded", "i": i})
        )
        self.written.audit.append(time.time())

    async def run(self, seconds: float, stop: asyncio.Event) -> int:
        i, end = 0, time.monotonic() + seconds
        while time.monotonic() < end and not stop.is_set():
            try:
                await self.once(i)
            except (psycopg.OperationalError, OSError):
                return i  # the database is gone: unacknowledged writes don't count
            i += 1
            await asyncio.sleep(1.0)
        return i


async def verify(key: bytes, written: Written) -> tuple[dict[str, Any], float]:
    """Check the restored database; returns findings and the newest surviving write."""
    cipher = FieldCipher(key)
    checkpoints = PostgresCheckpointer(app_dsn(), cipher)
    runs = PostgresRunStore(app_dsn(), cipher)
    memory = PgVectorMemoryStore(app_dsn(), HashingEmbedder(), cipher)
    chain = await PostgresAuditSink(app_dsn()).check()
    found: dict[str, Any] = {
        "audit_chain_ok": chain.ok,
        "audit_rows": chain.records,
        "problems": [],
    }
    if not chain.ok:
        found["problems"].append(f"audit chain: {chain.reason}")
    survived: list[float] = written.audit[: chain.records]
    recovered_points = recovered_runs = 0
    for run_id, (at, thread) in written.runs.items():
        rec = await runs.record(run_id)
        if rec is None:
            continue
        if not await runs.events(run_id):
            found["problems"].append(f"run {run_id} restored without its events")
        recovered_runs += 1
        survived.append(at)
        points = await checkpoints.list(TENANT, thread)
        for p in points:
            expected = written.checkpoints.get(p.checkpoint_id)
            if expected is None or p.state != expected[1]:
                found["problems"].append(
                    f"checkpoint {p.checkpoint_id} differs from what was written"
                )
            else:
                survived.append(expected[0])
                recovered_points += 1
    scope = MemoryScope(tenant_id=TENANT, user_id=USER, agent_id="dr-drill")
    items = await memory.items(scope, limit=10_000)
    for item in items:
        expected_mem = written.memories.get(item.id)
        if expected_mem is None or expected_mem[1] != item.content:
            found["problems"].append(f"memory {item.id} differs from what was written")
        else:
            survived.append(expected_mem[0])
    if items and not await memory.search(scope, items[0].content, k=1):
        found["problems"].append("memory search returned nothing")
    found["runs_recovered"] = recovered_runs
    found["checkpoints_recovered"] = recovered_points
    found["memories_recovered"] = len(items)
    return found, max(survived) if survived else 0.0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="dr-drill")
    p.add_argument("--write-seconds", type=float, default=90.0, help="writes before the disaster")
    args = p.parse_args(argv)
    policy = yaml.safe_load((ROOT / "governance" / "dr.yaml").read_text())
    target = policy["data"]["agent-database"]
    archive_timeout = int(policy["backup"]["archive_timeout_seconds"])
    key = os.urandom(32)
    cleanup()
    for v in ("archive", "base"):
        docker("volume", "create", VOLUMES[v])
        docker("run", "--rm", "-v", f"{VOLUMES[v]}:/x", IMAGE, "chown", "postgres:postgres", "/x")
    try:
        start(VOLUMES["data"], archive_timeout)
        wait_ready()
        provision(admin_dsn(), APP_PASSWORD)
        migrate(app_dsn())
        workload = Workload(key)
        asyncio.run(workload.once(-1))  # something before the base backup
        docker(
            "exec",
            "-u",
            "postgres",
            NAME,
            "pg_basebackup",
            "-D",
            "/base/b",
            "-X",
            "none",
            "-c",
            "fast",
        )
        print(f"dr-drill: base backup taken; writing for {args.write_seconds:.0f} s")
        asyncio.run(workload.run(args.write_seconds, asyncio.Event()))
        # the disaster: the database and its disk are gone, while the workload would go on
        disaster = time.time()
        docker("rm", "-f", NAME)
        docker("volume", "rm", "-f", VOLUMES["data"])
        print(
            f"dr-drill: database and data volume destroyed at {datetime.fromtimestamp(disaster, UTC):%H:%M:%S}"
        )
        # restore: base backup + archived WAL, to the end of the archive
        docker("volume", "create", VOLUMES["restored"])
        docker(
            "run", "--rm", "-v", f"{VOLUMES['base']}:/base", "-v", f"{VOLUMES['restored']}:/data",
            IMAGE, "bash", "-c",
            "cp -a /base/b/. /data/ && touch /data/recovery.signal && chown -R postgres:postgres /data && chmod 700 /data",
        )  # fmt: skip
        start(VOLUMES["restored"], archive_timeout, recovering=True)
        wait_ready()
        found, newest = asyncio.run(verify(key, workload.written))
        verified = time.time()
    finally:
        cleanup()
    acked = workload.written.acked()
    rpo = round(disaster - newest, 1)
    rto = round(verified - disaster, 1)
    lost = sum(1 for t in acked if t > newest)
    result = {
        "drill": "agent-database",
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "image": IMAGE,
        "archive_timeout_seconds": archive_timeout,
        "writes_acknowledged": len(acked),
        "writes_lost": lost,
        "measured": {"rpo_seconds": rpo, "rto_seconds": rto},
        "targets": {"rpo_seconds": target["rpo_seconds"], "rto_seconds": target["rto_seconds"]},
        "verification": found,
    }
    ok = (
        not found["problems"]
        and found["audit_chain_ok"]
        and rpo <= target["rpo_seconds"]
        and rto <= target["rto_seconds"]
    )
    result["passed"] = ok
    out = ROOT / "evals" / "results" / "dr"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"agent-database-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print(f"dr-drill: {'PASSED' if ok else 'FAILED'} ({path.relative_to(ROOT)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
