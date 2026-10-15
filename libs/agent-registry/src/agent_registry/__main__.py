"""``just agents``: list every agent with its lifecycle status, and change status.

    just agents                         # sync agents/ into the registry and list
    just agents --approve NAME VERSION --by carol --reason "evals pass" [--expires 2027-01-31]
    just agents --deprecate | --retire NAME VERSION --by carol
    just agents --backstage catalog/agents   # use the Backstage catalogue instead

The registry comes from the environment (``agent_registry.factory``): Postgres when
``AGENT_DB_DSN`` is set, otherwise a local SQLite file, ``.agents.db``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from datetime import UTC, datetime

from agent_contracts.interfaces import AgentRegistry
from agent_contracts.models import AgentRecord, AgentStatus

from agent_registry import factory
from agent_registry.sync import load_all, sync


def open_registry(backstage: str | None = None) -> tuple[AgentRegistry, str]:
    if backstage:
        return factory.open_registry(
            {**os.environ, "AGENT_REGISTRY": "backstage", "AGENT_REGISTRY_CATALOG": backstage}
        )
    return factory.open_registry()


def table(records: Sequence[AgentRecord]) -> str:
    rows = [
        ("AGENT", "VERSION", "STATUS", "OWNER", "RISK", "AUTONOMY", "PATTERN", "EVALS", "TOOLS")
    ]
    for r in records:
        rows.append(
            (
                r.name,
                r.version,
                r.status.value,
                r.owner,
                r.risk_tier.value,
                r.autonomy,
                r.pattern,
                r.eval_status,
                str(len(r.tools)),
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join(
        "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) for row in rows
    )


async def run(args: argparse.Namespace) -> int:
    registry, where = open_registry(args.backstage)
    move = {
        "approve": AgentStatus.APPROVED,
        "deprecate": AgentStatus.DEPRECATED,
        "retire": AgentStatus.RETIRED,
    }
    for flag, status in move.items():
        target = getattr(args, flag)
        if target:
            expires = (
                datetime.fromisoformat(args.expires).replace(tzinfo=UTC) if args.expires else None
            )
            record = await registry.set_status(
                target[0],
                target[1],
                status,
                by=args.by or "",
                reason=args.reason,
                expires_at=expires,
            )
            print(f"{record.name}@{record.version}: {record.status.value} (by {record.updated_by})")
            return 0
    problems = [
        r
        for r in await sync(registry, load_all(args.agents_dir))
        if r.outcome.startswith("refused")
    ]
    records = await registry.list(include_retired=args.all)
    print(table(records))
    print(f"\n{len(records)} agent version(s) · registry: {where}")
    for p in problems:
        print(f"! {p.name}@{p.version} {p.outcome}")
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agents", description="Agent registry")
    p.add_argument("--agents-dir", default="agents")
    p.add_argument("--all", action="store_true", help="include retired versions")
    p.add_argument("--backstage", metavar="DIR", help="use the Backstage catalogue in DIR")
    for flag in ("approve", "deprecate", "retire"):
        p.add_argument(f"--{flag}", nargs=2, metavar=("NAME", "VERSION"))
    p.add_argument("--by", help="who is making the change (required for status changes)")
    p.add_argument("--reason", default="")
    p.add_argument("--expires", help="approval expiry date (ISO 8601)")
    return asyncio.run(run(p.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
