"""``spec_version`` migrations. Each function upgrades a raw document by exactly one version."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_spec.model import CURRENT_SPEC_VERSION

Migration = Callable[[dict[str, Any]], dict[str, Any]]


def _v0_to_v1(doc: dict[str, Any]) -> dict[str, Any]:
    """v0 (the draft layout) had ``graph`` as a bare node list, edges at the top level and tools
    as plain strings. v1 nests ``graph: {nodes, edges}`` and makes every tool an object."""
    out = dict(doc)
    graph = out.get("graph")
    if isinstance(graph, list):
        out["graph"] = {"nodes": graph, "edges": out.pop("edges", [])}
    out["tools"] = [{"ref": t} if isinstance(t, str) else t for t in out.get("tools", [])]
    out["spec_version"] = 1
    return out


MIGRATIONS: dict[int, Migration] = {0: _v0_to_v1}


def migrate(doc: dict[str, Any]) -> tuple[dict[str, Any], list[int]]:
    """Upgrade ``doc`` to the current version; returns the document and the versions passed."""
    version = doc.get("spec_version", 0)
    if not isinstance(version, int) or version < 0:
        raise ValueError(f"spec_version must be a whole number, got {version!r}")
    if version > CURRENT_SPEC_VERSION:
        raise ValueError(
            f"spec_version {version} is newer than this platform supports "
            f"({CURRENT_SPEC_VERSION}); upgrade agent-spec"
        )
    passed: list[int] = []
    while version < CURRENT_SPEC_VERSION:
        doc = MIGRATIONS[version](doc)
        passed.append(version)
        version = doc["spec_version"]
    return doc, passed
