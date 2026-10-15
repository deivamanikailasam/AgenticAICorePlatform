"""What the governance artefacts are generated from (Step 32): every agent spec in the repository,
the platform configuration, the tool registry's cards and the approvers file. Nothing here reads a
database or a network service, so the artefacts regenerate identically in CI."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from agent_registry.sync import spec_files
from agent_spec import AgentSpec, load_spec


@dataclass(frozen=True)
class Entry:
    """One spec: a top-level agent (a reference app) or one of its workers."""

    spec: AgentSpec
    path: Path  # relative to the repository root
    system: str  # the top-level agent it belongs to
    worker: bool

    @property
    def digest(self) -> str:
        body = json.dumps(self.spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()


@dataclass
class Inventory:
    root: Path
    entries: list[Entry]
    platform: dict[str, Any]
    tool_cards: dict[str, dict[str, Any]]  # ref -> tool card data
    approvers: dict[str, Any]
    eval_results: dict[str, dict[str, Any]] = field(default_factory=dict)  # agent -> latest run

    def systems(self) -> list[Entry]:
        return [e for e in self.entries if not e.worker]

    def workers_of(self, system: str) -> list[Entry]:
        return [e for e in self.entries if e.worker and e.system == system]

    def by_name(self, name: str) -> Entry:
        return next(e for e in self.entries if e.spec.name == name)


def load(root: Path, tool_cards: dict[str, dict[str, Any]]) -> Inventory:
    entries = []
    for path in spec_files(root / "agents"):
        rel = path.relative_to(root)
        worker = rel.parts[-2] == "workers"
        entries.append(Entry(load_spec(path), rel, rel.parts[1], worker))
    platform = yaml.safe_load((root / "platform.yaml").read_text()) or {}
    approvers = yaml.safe_load((root / "governance" / "approvers.yaml").read_text()) or {}
    results: dict[str, dict[str, Any]] = {}
    # a recorded eval run is evals/results/<agent>.json, written by an eval command; only real
    # runs are cited, never a placeholder number
    for f in sorted((root / "evals" / "results").glob("*.json")):
        results[f.stem] = json.loads(f.read_text())
    return Inventory(root, entries, platform, tool_cards, approvers, results)
