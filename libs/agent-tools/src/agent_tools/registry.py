"""Tool registries: in-memory (tests) and Postgres-backed (default, shared across processes).

The Postgres registry stores every *spec* (name, version, schemas, side effect, owner, deprecation)
so all API and worker processes agree on what exists and what is deprecated. The *implementation*
of a tool is bound in the process that registers it; looking up a tool that is registered but
not bound in this process is an explicit error rather than a silent fallback.
"""

from __future__ import annotations

from collections.abc import Sequence

import psycopg
from agent_contracts.interfaces import Tool, ToolRegistry
from agent_contracts.models import ToolSpec


def version_key(version: str) -> tuple[int, ...]:
    core = version.split("-", 1)[0]
    return tuple(int(p) if p.isdigit() else 0 for p in core.split("."))


class InMemoryToolRegistry(ToolRegistry):
    """Tools in a dictionary. For unit tests and single-process tools."""

    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], Tool] = {}
        self._deprecated: set[tuple[str, str]] = set()

    async def register(self, tool: Tool) -> None:
        key = (tool.spec.name, tool.spec.version)
        if key in self._tools:
            raise ValueError(f"tool {key[0]}@{key[1]} already registered")
        self._tools[key] = tool

    async def get(self, name: str, version: str | None = None) -> Tool:
        if version is not None:
            if (name, version) not in self._tools:
                raise KeyError(f"{name}@{version}")
            return self._tools[(name, version)]
        live = [k for k in self._tools if k[0] == name and k not in self._deprecated]
        if not live:
            raise KeyError(name)
        return self._tools[max(live, key=lambda k: version_key(k[1]))]

    async def list(self, include_deprecated: bool = False) -> Sequence[ToolSpec]:
        keys = sorted(self._tools, key=lambda k: (k[0], version_key(k[1])))
        return [
            self._tools[k].spec.model_copy(update={"deprecated": k in self._deprecated})
            for k in keys
            if include_deprecated or k not in self._deprecated
        ]

    async def deprecate(self, name: str, version: str) -> None:
        if (name, version) not in self._tools:
            raise KeyError(f"{name}@{version}")
        self._deprecated.add((name, version))


class PostgresToolRegistry(ToolRegistry):
    """Specs in ``agent_tools.tool_specs`` (Project 0 Postgres, ``agent`` DB); bindings in process."""

    def __init__(self, dsn: str, namespace: str = "default") -> None:
        self._dsn = dsn
        self.namespace = namespace
        self._bound: dict[tuple[str, str], Tool] = {}

    async def _conn(self) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)

    async def register(self, tool: Tool) -> None:
        spec = tool.spec
        async with await self._conn() as conn:
            cur = await conn.execute(
                "insert into agent_tools.tool_specs (namespace, name, version, spec, owner, side_effect)"
                " values (%s, %s, %s, %s, %s, %s) on conflict do nothing",
                (
                    self.namespace,
                    spec.name,
                    spec.version,
                    spec.model_dump_json(),
                    spec.owner,
                    spec.side_effect.value,
                ),
            )
            if cur.rowcount == 0:
                raise ValueError(
                    f"tool {spec.name}@{spec.version} already registered in '{self.namespace}'"
                )
        self._bound[(spec.name, spec.version)] = tool

    def bind(self, tool: Tool) -> None:
        """Attach an implementation for a spec another process registered."""
        self._bound[(tool.spec.name, tool.spec.version)] = tool

    async def _versions(self, name: str) -> list[tuple[str, bool]]:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "select version, deprecated from agent_tools.tool_specs where namespace = %s and name = %s",
                (self.namespace, name),
            )
            return [(r[0], r[1]) for r in await cur.fetchall()]

    async def get(self, name: str, version: str | None = None) -> Tool:
        versions = await self._versions(name)
        if version is None:
            live = [v for v, dep in versions if not dep]
            if not live:
                raise KeyError(name)
            version = max(live, key=version_key)
        elif version not in {v for v, _ in versions}:
            raise KeyError(f"{name}@{version}")
        tool = self._bound.get((name, version))
        if tool is None:
            raise KeyError(
                f"{name}@{version} is registered but has no implementation in this process"
            )
        return tool

    async def list(self, include_deprecated: bool = False) -> Sequence[ToolSpec]:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "select spec, deprecated from agent_tools.tool_specs"
                " where namespace = %s and (%s or not deprecated)",
                (self.namespace, include_deprecated),
            )
            rows = await cur.fetchall()
        specs = [
            ToolSpec.model_validate(r[0]).model_copy(update={"deprecated": r[1]}) for r in rows
        ]
        return sorted(specs, key=lambda s: (s.name, version_key(s.version)))

    async def deprecate(self, name: str, version: str) -> None:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "update agent_tools.tool_specs set deprecated = true where namespace = %s and name = %s and version = %s",
                (self.namespace, name, version),
            )
            if cur.rowcount == 0:
                raise KeyError(f"{name}@{version}")
