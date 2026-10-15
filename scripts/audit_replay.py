"""Reconstruct a run from the audit trail plus its checkpoints (Step 31).

Prints who ran which agent at which versions, the plan, every audited event and checkpoint in
time order, and the cross-checks between the two sources. Exit status 0 when every check passes,
1 when one fails, 2 when the trail has no record of the run.

    just audit-replay <run_id>

The trail is ``AGENT_AUDIT_DSN`` (default ``AGENT_DB_DSN``); the checkpoints are the agent
database's (``AGENT_DB_DSN``), decrypted with the checkpoint key from OpenBao (or, in dev and CI
only, ``SECRET__AGENT_PLATFORM__CHECKPOINTS__KEY``).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

from agent_audit.postgres import PostgresAuditSink
from agent_audit.reconstruct import replay
from agent_contracts.interfaces import Checkpointer
from agent_runtime.checkpoints import FieldCipher, PostgresCheckpointer
from agent_tools.secrets import default_resolver

KEY = "${secret:agent-platform/checkpoints#key}"


async def run(run_id: str, audit_dsn: str, db_dsn: str | None) -> int:
    checkpointer: Checkpointer | None = None
    if db_dsn:
        key = await default_resolver().resolve(os.environ.get("AGENT_CHECKPOINT_KEY", KEY))
        checkpointer = PostgresCheckpointer(db_dsn, FieldCipher.from_b64(key))
    story = await replay(run_id, PostgresAuditSink(audit_dsn), checkpointer)
    if not story.steps:
        print(f"no audit records for run {run_id}", file=sys.stderr)
        return 2
    print(story.render())
    return 0 if story.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id")
    parser.add_argument("--no-checkpoints", action="store_true", help="the audit trail only")
    args = parser.parse_args(argv)
    db_dsn = os.environ.get("AGENT_DB_DSN")
    audit_dsn = os.environ.get("AGENT_AUDIT_DSN") or db_dsn
    if not audit_dsn:
        print("set AGENT_AUDIT_DSN or AGENT_DB_DSN", file=sys.stderr)
        return 2
    return asyncio.run(run(args.run_id, audit_dsn, None if args.no_checkpoints else db_dsn))


if __name__ == "__main__":
    sys.exit(main())
