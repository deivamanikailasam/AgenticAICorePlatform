"""From ``agent.yaml`` specs to registry records.

Every spec version is registered as ``draft`` the first time it is seen. Its ``spec_hash`` is a
SHA-256 of the canonical spec, so editing a spec without bumping its version is caught: the
registry refuses the changed content under the reviewed version.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_contracts.interfaces import AgentRegistry
from agent_contracts.models import AgentRecord, RiskTier
from agent_spec import AgentSpec, load_spec


def spec_hash(spec: AgentSpec) -> str:
    canonical = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def card_of(spec: AgentSpec) -> dict[str, Any]:
    """The registry's summary card. Full agent cards (evals, limitations, lineage) are Step 32."""
    return {
        "kind": "agent",
        "name": spec.name,
        "version": spec.version,
        "owner": spec.owner,
        "purpose": spec.description,
        "risk_tier": spec.risk_tier.value,
        "autonomy": spec.autonomy.value,
        "pattern": spec.pattern.value,
        "tools": [t.ref for t in spec.tools],
        "agents": [a.name for a in spec.agents],
        "published": {"a2a": spec.interop.a2a_publish, "mcp": spec.interop.mcp_publish},
    }


def record_of(spec: AgentSpec, by: str = "sync") -> AgentRecord:
    return AgentRecord(
        name=spec.name,
        version=spec.version,
        owner=spec.owner,
        description=spec.description,
        risk_tier=RiskTier(spec.risk_tier.value),
        autonomy=spec.autonomy.value,
        pattern=spec.pattern.value,
        tools=tuple(t.ref for t in spec.tools),
        agents=tuple(a.name for a in spec.agents),
        card=card_of(spec),
        spec_hash=spec_hash(spec),
        updated_by=by,
    )


@dataclass(frozen=True)
class SyncResult:
    name: str
    version: str
    outcome: str  # registered | unchanged | refused: <reason>


def spec_files(agents_dir: str | Path) -> list[Path]:
    """Every spec in the directory: ``<agent>/agent.yaml`` and team members ``<agent>/workers/*``."""
    root = Path(agents_dir)
    return sorted([*root.glob("*/agent.yaml"), *root.glob("*/workers/*.yaml")])


async def sync(
    registry: AgentRegistry, specs: Iterable[AgentSpec], by: str = "sync"
) -> list[SyncResult]:
    results = []
    for spec in specs:
        try:
            before = await registry.get(spec.name, spec.version)
        except KeyError:
            before = None
        try:
            await registry.register(record_of(spec, by))
        except ValueError as exc:
            results.append(SyncResult(spec.name, spec.version, f"refused: {exc}"))
            continue
        results.append(SyncResult(spec.name, spec.version, "unchanged" if before else "registered"))
    return results


def load_all(agents_dir: str | Path) -> list[AgentSpec]:
    return [load_spec(p) for p in spec_files(agents_dir)]
