"""``local://platform/sql_read``: named, parameterised read-only queries (Step 29).

The model picks a query by name and supplies its parameters; it never writes SQL. The queries
come from the tool's ``config`` in the agent's spec. Each call logs in to Postgres with the
short-lived database user OpenBao issued for that call (``credentials:`` on the tool), inside a
read-only transaction with a statement timeout. The user's grants (a read-only group role)
are the real limit; the checks here are a second layer.

```yaml
tools:
  - ref: local://platform/sql_read
    credentials: agent-db/creds/tool-reader
    config:
      database: agent                # host: AGENT_TOOL_DB_HOST (default 127.0.0.1:5432)
      queries:
        supplier_by_name: "select name, abn, status from tool_data.suppliers where name = %(name)s"
```
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any, ClassVar

from agent_contracts.interfaces import Tool
from agent_contracts.models import DataClass, RunContext, SideEffect, ToolSpec

from agent_tools.credentials import require_credential

READ_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
PARAM = re.compile(r"%\((\w+)\)s")


class SqlReadTool(Tool):
    example_arguments: ClassVar[dict[str, Any]] = {
        "query": "supplier_by_name",
        "params": {"name": "Acme Pty Ltd"},
    }

    def __init__(
        self,
        queries: Mapping[str, str] | None = None,
        database: str = "agent",
        host: str | None = None,
        name: str = "sql_read",
        max_rows: int = 50,
        statement_timeout_ms: int = 5000,
    ) -> None:
        self.queries = dict(queries or {})
        for qname, text in self.queries.items():
            if not READ_ONLY.match(text) or ";" in text.strip().rstrip(";"):
                raise ValueError(f"query {qname} must be a single SELECT (or WITH ... SELECT)")
        self.database = database
        self.host = host or os.environ.get("AGENT_TOOL_DB_HOST", "127.0.0.1:5432")
        self.name = name
        self.max_rows = max_rows
        self.statement_timeout_ms = statement_timeout_ms

    @property
    def spec(self) -> ToolSpec:
        names = sorted(self.queries)
        params = sorted({p for q in self.queries.values() for p in PARAM.findall(q)})
        return ToolSpec(
            name=self.name,
            ref="local://platform/sql_read",
            version="1.0.0",
            description=(
                "Run one of these read-only database queries by name: "
                + (", ".join(names) or "(none configured)")
                + ". Pass its parameters in 'params'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", **({"enum": names} if names else {})},
                    "params": {
                        "type": "object",
                        "properties": {p: {"type": ["string", "number"]} for p in params},
                        "additionalProperties": False,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            side_effect=SideEffect.READ,
            idempotent=True,
            timeout_seconds=10,
            data_classification=DataClass.INTERNAL,
            owner="agent-platform",
        )

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        import psycopg
        from psycopg.rows import dict_row

        text = self.queries.get(str(arguments["query"]))
        if text is None:
            raise ValueError(f"unknown query {arguments['query']}")
        params = dict(arguments.get("params") or {})
        missing = set(PARAM.findall(text)) - set(params)
        if missing:
            raise ValueError(f"missing parameters: {', '.join(sorted(missing))}")
        cred = require_credential(self.name)
        conn = await psycopg.AsyncConnection.connect(
            host=self.host.rsplit(":", 1)[0],
            port=int(self.host.rsplit(":", 1)[1]) if ":" in self.host else 5432,
            dbname=self.database,
            user=cred.secret("username"),
            password=cred.secret("password"),
            connect_timeout=5,
            row_factory=dict_row,
        )
        async with conn:
            await conn.set_read_only(True)
            async with conn.transaction():
                await conn.execute(
                    f"set local statement_timeout = {int(self.statement_timeout_ms)}"
                )
                cur = await conn.execute(text, params)
                rows = await cur.fetchmany(self.max_rows + 1)
        return {
            "rows": [{k: _plain(v) for k, v in r.items()} for r in rows[: self.max_rows]],
            "truncated": len(rows) > self.max_rows,
        }


def _plain(value: Any) -> Any:
    return value if value is None or isinstance(value, str | int | float | bool) else str(value)
