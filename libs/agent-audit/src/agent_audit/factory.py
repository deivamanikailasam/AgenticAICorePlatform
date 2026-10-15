"""Which audit trail a service writes to (Step 31).

``AGENT_AUDIT_DSN`` set: the hash-chained Postgres trail (migration 0008), shared by every
service. Otherwise the service's local hash-chained JSONL file, which is allowed only in dev and
CI: in staging and prod a service without the shared trail refuses to start. The switch is its own
variable, not ``AGENT_DB_DSN``, so a service that only needs the agent database for runs or memory
doesn't silently move its audit trail.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from agent_contracts.dummies.observability import JsonlAuditSink
from agent_contracts.interfaces import AuditSink

from agent_audit.postgres import PostgresAuditSink

DEV_STAGES = frozenset({"dev", "ci", "test"})


def audit_sink_from_env(default_file: str, env: Mapping[str, str] | None = None) -> AuditSink:
    e = os.environ if env is None else env
    dsn = e.get("AGENT_AUDIT_DSN")
    if dsn:
        return PostgresAuditSink(dsn)
    stage = e.get("AGENT_ENV", "dev")
    if stage not in DEV_STAGES:
        raise RuntimeError(
            f"AGENT_ENV={stage}: the audit trail must be the shared Postgres trail; set "
            "AGENT_AUDIT_DSN"
        )
    return JsonlAuditSink(default_file)
