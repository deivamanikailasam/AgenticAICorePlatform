"""``local://platform/publish_report`` (Step 40): publish a finished report.

Publishing can't be taken back once readers have it, so the tool is ``irreversible`` and tagged
``external``. Policy (OPA) refuses it without approval evidence, and a spec that grants it must
require approval: the compiler puts an approval step in front of it, so a person approves
exactly the report that will be published (the arguments are digested into the approval).

The report goes to an artefact store under ``reports/<tenant>/<run>/``. The default store is
per process and in memory (dev and CI). A deployment passes a durable store, such as Project 0's
S3 (``agent_tools.artefacts.S3ArtefactStore``, credentials from OpenBao): VERIFY-ON-MAC.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent_contracts.interfaces import Tool
from agent_contracts.models import DataClass, RunContext, SideEffect, ToolSpec

from agent_tools.artefacts import ArtefactStore, MemoryArtefactStore

DEFAULT_STORE = MemoryArtefactStore()


class PublishReportTool(Tool):
    """Publishes a report and returns where it was published, with the report itself."""

    def __init__(self, store: ArtefactStore | None = None) -> None:
        self.store = store or DEFAULT_STORE

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="publish_report",
            ref="local://platform/publish_report",
            version="1.0.0",
            description="Publish a finished report to its readers. Needs a person's approval.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 300},
                    "report": {"type": "string", "minLength": 20, "maxLength": 20000},
                },
                "required": ["title", "report"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"published": {"type": "string"}, "report": {"type": "string"}},
                "required": ["published", "report"],
            },
            side_effect=SideEffect.IRREVERSIBLE,
            idempotent=False,
            timeout_seconds=10,
            data_classification=DataClass.INTERNAL,
            owner="agent-platform",
            tags=("external",),
        )

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        title, report = str(arguments["title"]), str(arguments["report"])
        body = json.dumps({"title": title, "report": report, "run_id": ctx.run_id}).encode()
        uri = await self.store.put(f"reports/{ctx.tenant_id}/{ctx.run_id}.json", body)
        return {"published": uri, "report": report}
