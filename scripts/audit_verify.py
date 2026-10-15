"""Verify the audit trail's hash chain (Step 31) and name the first broken record.

The shared trail is the Postgres table ``agent_audit.events`` (``AGENT_AUDIT_DSN``, default
``AGENT_DB_DSN``); a dev service's local trail is a JSONL file. Exit status 0 means the whole
chain verifies, 1 means it is broken (the output says where and how), 2 means nothing to check.

    just audit-verify                                 # the shared Postgres trail
    just audit-verify --file /tmp/agent-api-audit.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from agent_audit.postgres import PostgresAuditSink
from agent_audit.verify import Verdict, check_records
from agent_contracts.models import AuditRecord


def check_file(path: Path) -> Verdict:
    lines = [line for line in path.read_text().splitlines() if line]
    return check_records(AuditRecord.model_validate_json(line) for line in lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, action="append", help="a JSONL trail (repeatable)")
    parser.add_argument("--dsn", help="the Postgres trail (default AGENT_AUDIT_DSN, AGENT_DB_DSN)")
    args = parser.parse_args(argv)

    targets: list[tuple[str, Verdict]] = []
    for path in args.file or []:
        targets.append((str(path), check_file(path)))
    if not args.file:
        dsn = args.dsn or os.environ.get("AGENT_AUDIT_DSN") or os.environ.get("AGENT_DB_DSN")
        if not dsn:
            print(
                "no trail to check: pass --file or --dsn, or set AGENT_AUDIT_DSN", file=sys.stderr
            )
            return 2
        targets.append(("agent_audit.events", asyncio.run(PostgresAuditSink(dsn).check())))

    broken = False
    for name, v in targets:
        if v.ok:
            print(f"{name}: OK, {v.records} records, chain intact")
        else:
            broken = True
            print(f"{name}: BROKEN at record {v.first_broken}: {v.reason} ({v.records} verified)")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
