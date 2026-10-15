"""The external-catalogue alternative: the registry kept as Backstage catalog entities in Git.

Each agent version is one ``Component`` entity (``spec.type: ai-agent``) in
``<directory>/<agent>/<version>.yaml``; Backstage ingests the directory through a ``Location``.
The full record lives in the ``agent-platform.io/record`` annotation (the platform's source of
truth); ``spec.lifecycle``, ``spec.owner``, tags and labels are derived from it so the catalogue
shows the state. Status history is appended to ``<directory>/history.jsonl``.

Lifecycle changes go through this class (or ``just agents --approve``) and land as file changes,
so in a GitOps setup an approval is a reviewed pull request.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from agent_contracts.dummies.registry import InMemoryAgentRegistry
from agent_contracts.models import AgentRecord, AgentStatus, AgentStatusChange

ANNOTATION = "agent-platform.io/record"
LIFECYCLE = {
    AgentStatus.DRAFT: "experimental",
    AgentStatus.APPROVED: "production",
    AgentStatus.DEPRECATED: "deprecated",
    AgentStatus.RETIRED: "retired",
}


def entity(record: AgentRecord) -> dict[str, Any]:
    """A Backstage ``Component`` entity for one agent version."""
    return {
        "apiVersion": "backstage.io/v1alpha1",
        "kind": "Component",
        "metadata": {
            "name": f"{record.name}-{record.version}",
            "title": f"{record.name} {record.version}",
            "description": record.description or record.name,
            "tags": [
                "ai-agent",
                f"risk-{record.risk_tier.value}",
                record.pattern.replace("_", "-"),
            ],
            "labels": {"agent-platform.io/status": record.status.value},
            "annotations": {ANNOTATION: record.model_dump_json()},
        },
        "spec": {
            "type": "ai-agent",
            "lifecycle": LIFECYCLE[record.status],
            "owner": record.owner,
            "system": "agent-platform",
            **({"dependsOn": [f"component:{a}" for a in record.agents]} if record.agents else {}),
        },
    }


class BackstageCatalogRegistry(InMemoryAgentRegistry):
    def __init__(self, directory: str = "catalog/agents") -> None:
        super().__init__()
        self.directory = Path(directory)
        for path in sorted(self.directory.glob("*/*.yaml")):
            doc = yaml.safe_load(path.read_text())
            record = AgentRecord.model_validate_json(doc["metadata"]["annotations"][ANNOTATION])
            self._records[(record.name, record.version)] = record
        history = self.directory / "history.jsonl"
        if history.exists():
            self._history = [
                AgentStatusChange.model_validate_json(line)
                for line in history.read_text().splitlines()
                if line.strip()
            ]

    def _save(self, record: AgentRecord, logged: int) -> AgentRecord:
        path = self.directory / record.name / f"{record.version}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(entity(record), sort_keys=False))
        if len(self._history) > logged:
            with (self.directory / "history.jsonl").open("a") as f:
                for change in self._history[logged:]:
                    f.write(change.model_dump_json() + "\n")
        return record

    async def register(self, record: AgentRecord) -> AgentRecord:
        logged = len(self._history)
        return self._save(await super().register(record), logged)

    async def set_status(
        self,
        name: str,
        version: str,
        status: AgentStatus,
        by: str,
        reason: str = "",
        expires_at: datetime | None = None,
    ) -> AgentRecord:
        logged = len(self._history)
        updated = await super().set_status(name, version, status, by, reason, expires_at)
        return self._save(updated, logged)

    async def set_eval_status(self, name: str, version: str, eval_status: str) -> AgentRecord:
        return self._save(
            await super().set_eval_status(name, version, eval_status), len(self._history)
        )


def location(directory: str = "catalog/agents") -> str:
    """A Backstage ``Location`` entity that ingests every agent entity in ``directory``."""
    return yaml.safe_dump(
        {
            "apiVersion": "backstage.io/v1alpha1",
            "kind": "Location",
            "metadata": {"name": "agent-platform-agents"},
            "spec": {"targets": [f"./{directory}/*/*.yaml"]},
        },
        sort_keys=False,
    )
